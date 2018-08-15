#Check which jacobian we are returning from compute_gradient and if its returning jacobian transpose??
#Change exit criteria
#Vectorize derivatives finding wherever possible
#Make sure cost isnt calculated twice while finding jacobian
#Make sure values are in float before finding inverse
'''
Camera Pose Estimation
'''

# Libraries
import numpy as np 
import cv2
import tensorflow as tf
import sys
import time
import argparse
import math

# Modules
import depth_map_fusion as depth_map_fusion
#import monodepth

'''
Variable nomenclature:

* u : high grad elements
* uu: array of high elements
* T: pose (3*4)
* T_s: pose compressed (6x1)
* D: depth map
* U: uncertainity
* dof: len(uu)

'''

im_size = (480,640)
sigma_p = 5 # Some white noise variance thing
index_matrix = np.dstack(np.meshgrid(np.arange(480),np.arange(640),indexing = 'ij'))
cam_matrix = np.eye(3,3) #Change later
cam_matrix_inv = np.eye(3,3) #Change later

class Keyframe:
	def __init__(self, pose, depth, uncertainty, image):
		self.T = pose # 4x4 transformation matrix # 6 vector
		self.D = depth
		self.U = uncertainty
		self.I = image

def isRotationMatrix(R) :
	'''
	Checks if a matrix is a valid rotation matrix.
	'''
	Rt = np.transpose(R)
	#print R,'\n\n\n'
	shouldBeIdentity = np.dot(Rt, R)
	I = np.identity(3, dtype = R.dtype)
	n = np.linalg.norm(I - shouldBeIdentity)
	return n < 1e-6
	

def extract_angles(R):
	'''
	Extract rotation angles

	Returns: aplha, beta, gamma (as np array)
	'''

	assert(isRotationMatrix(R)) #Throws error if false
     
	sy = math.sqrt(R[0,0] * R[0,0] +  R[1,0] * R[1,0])
		
	singular = sy < 1e-6
	
	if  not singular :
		x = math.atan2(R[2,1] , R[2,2])
		y = math.atan2(-R[2,0], sy)
		z = math.atan2(R[1,0], R[0,0])
	else :
		x = math.atan2(-R[1,2], R[1,1])
		y = math.atan2(-R[2,0], sy)
		z = 0
	
	return np.array([x, y, z])


def get_min_rep(T):
	'''
	Convert 3*4 matrix into 6*1 vector

	[x y z alpha beta gamma]
	
	'''
	t=T[:,3]
	x,y,z=t

	angles=extract_angles(T[:,:3])

	T_vect=np.zeros(6)
	T_vect[:3]=t
	T_vect[3:6]=angles
	return T_vect

def eulerAnglesToRotationMatrix(theta) :
	'''
	Converts rotation angles about x,y and z axis to a rotation matrix
	'''
	R_x = np.array([[1,         0,                  0                   ],
                    [0,         math.cos(theta[0]), -math.sin(theta[0]) ],
                    [0,         math.sin(theta[0]), math.cos(theta[0])  ]
                    ])
         
	R_y = np.array([[math.cos(theta[1]),    0,      math.sin(theta[1])  ],
                    [0,                     1,      0                   ],
                    [-math.sin(theta[1]),   0,      math.cos(theta[1])  ]
                    ])
                 
	R_z = np.array([[math.cos(theta[2]),    -math.sin(theta[2]),    0],
                    [math.sin(theta[2]),    math.cos(theta[2]),     0],
                    [0,                     0,                      1]
                    ])                     
	R = np.dot(R_z, np.dot( R_y, R_x ))
 
	return R

def _get_back_T(T_fl):
	'''
	Converts the minimal representation of the pose into the normal 3x4 transformation matrix
	'''
	#print "The flattened pose input is ",T_fl,'\n\n\n'
	T = np.ones((3,4))
	T[:,3] = T_fl[:3]
	R = eulerAnglesToRotationMatrix(T_fl[3:6])
	T[:,:3] = R
	return T

def find_uncertainty(u,D,D_prev,T):
	'''
	Finds uncertainty for one element of new keyframe

	Arguments:
		u: Pixel location
		D: New keyframe's depth map
		D_prev: Previous keyframe's depth map
		T: Pose of new keyframe

	Returns: Uncertainty at position u
	'''
	u=np.append(u,np.ones(1)) #Convert to homogeneous

	V = D * np.matmul(cam_matrix_inv,u) #World point
	V=np.append(V,np.ones(1))

	u_prop = np.matmul(cam_matrix,T)
	u_prop = np.matmul(u_prop,V)
	u_prop = u_prop/u_prop[2]
	u_prop=u_prop[:-1]

	U = D[u[0]][u[1]] - D_prev[u_prop[0]][u_prop[1]]
	return U**2

def get_uncertainty(T,D,prev_keyframe):
	'''
	Finds the uncertainty map for a new keyframe

	Arguments:
		T: Pose of new keyframe
		D: Depth map of new keyframe
		prev_keyframe: Previous keyframe of Keyframe class

	Returns:
		U: Uncertainty map
	'''
	T = np.matmul(np.linalg.inv(T),prev_keyframe.T) #Check if this is right
	find_uncertainty_v = np.vectorize(find_uncertainty)
	U = find_uncertainty_v(index_matrix,D,prev_keyframe.D,T) #Check
	return U

def get_initial_uncertainty(): 
	'''
	To get uncertainty map for the first frame
	'''
	raise NotImplementedError

def get_initial_pose(): 
	'''
	Pose for the first frame
	'''
	raise NotImplementedError

def get_highgrad_element(img,threshold=100):
	'''
	Finds high gradient areas in the image

	Arguments:
		img: Input image

	Returns:
		u: Array of pixel locations
		Shape (X,2)
		X: number of high grad elements
	'''
	
	laplacian = cv2.Laplacian(img,cv2.CV_8U)
	ret,thresh = cv2.threshold(laplacian,threshold,255,cv2.THRESH_BINARY)
	u = cv2.findNonZero(thresh)
	return np.squeeze(np.array(u))

def calc_photo_residual(i,frame,cur_keyframe,T):
	'''
	Calculates the photometric residual for one point

	Arguments:
		i: Pixel location
		frame: Current frame as numpy array
		cur_keyframe: Previous keyframe as Keyframe object
		T: Estimated pose

	Returns:
		r: Photometric residual
	'''
	# Make i homogeneous
	i = np.append(i,np.ones(1)) 
	i = i.astype(int)
	#3D point 3*1
	V = cur_keyframe.D[i[0]][[1]] * np.matmul(cam_matrix_inv,i) 

	#Make V homogeneous 4*1
	V=np.append(V,1)

	#3D point in the real world shifted (3*4 x 4*1 = 3*1)
	u_prop = np.matmul(T,V)[:3]

	#3D point in camera frame (3*3 * 3*1)
	u_prop = np.matmul(cam_matrix,u_prop) 

	# Projection onto image plane
	u_prop = (u_prop/u_prop[2])[:2] 
	u_prop = u_prop.astype(int)
	# Residual width*height

	r = (cur_keyframe.I[i[0]][i[1]] - frame[u_prop[0]][u_prop[1]])

	return r

#Not needed?
"""
def calc_photo_residual_d(u,D,T,frame,cur_keyframe): #For finding the derivative only
	'''
	Calculates photometric residual but only for finding the derivative

	Arguments:
		u: High gradient pixel location
		D: Depth value in previous keyframe at u
		T: Estimated pose
		frame: current frame as numpy array
		cur_keyframe: Previous keyframe as a Keyframe object

	Returns:
		r: Photometric residual
	'''
	u = np.append(u,np.ones(1))
	u = u.astype(int)
	Vp = D*np.matmul(cam_matrix_inv,u)
	Vp = tf.reshape(tf.concat([Vp,tf.constant(np.array([1],np.float64))],0),[4,1]) # 4x1
	T_t = tf.constant(T) # 3x4
	
	u_prop = tf.matmul(T_t,Vp)[:3] #3x1
	
	u_prop = tf.matmul(tf.constant(cam_matrix),u_prop)
	u_prop = (u_prop/u_prop[2])[:2]
	u_prop = tf.cast(u_prop,tf.int32)
	r = cur_keyframe.I[u[0]][u[1]] - frame[u_prop[0]][u_prop[1]]
	return r 
"""

def get_delD(D):
	return 0.01 #Change later to calculate based on input depth map


def delr_delD(u,frame,cur_keyframe,T):
	'''
	Finds the derivative of the photometric residual wrt depth (r wrt d)
	delr/delD  = (delr/delu)*(delu/delD)

	Arguments:
		u: High gradient pixel location
		frame: Current frame as numpy array
		cur_keyframe: Previous keyframe as a Keyframe object
		T: Estimated pose

	Returns:
		delr: The derivative
	'''
	#Convert u to int
	u = u.astype(int)

	#For finding right and left sides
	u1 = u - 1
	u2 = u + 1

	#Depth map value at u-1 and u+1
	D1 = cur_keyframe.D[u1[0]][u1[1]]
	D2 = cur_keyframe.D[u2[0]][u2[1]]

	#Use D to calculate u_prop1 and u_prop2
	u1 = np.append(u1,np.ones(1))
	V1 = D1*np.matmul(cam_matrix_inv,u1)
	V1 = np.append(V1,np.ones(1))
	u_prop1 = np.matmul(T,V1)[:3] #3x1
	u_prop1 = np.matmul(cam_matrix,u_prop1)
	u_prop1 = ((u_prop1/u_prop1[2])[:2]).astype(int) #Propagated pixel location

	u2 = np.append(u2,np.ones(1))
	V2= D2*np.matmul(cam_matrix_inv,u2)
	V2 = np.append(V2,np.ones(1))
	u_prop2 = np.matmul(T,V2)[:3] #3x1
	u_prop2 = np.matmul(cam_matrix,u_prop2)
	u_prop2 = ((u_prop2/u_prop2[2])[:2]).astype(int) #Propagated pixel location


	#r = cur_keyframe.I[u[0]][u[1]] - frame[u_prop[0]][u_prop[1]] - How r is defined normally

	u1 = u1.astype(int)
	u2 = u2.astype(int)

	r1 = cur_keyframe.I[u1[0]-1,u1[1]-1] - frame[u_prop1[0],u_prop1[1]] # Left side
	r2 = cur_keyframe.I[u2[0]-1,u2[1]-1] - frame[u_prop2[0],u_prop2[1]] # Right side

	delrdelu = (r2 - r1)/2

	print "delr/delu = ",delrdelu,'\n'

	delddelu = float((D2 - D1)/2)
	deludeld = 1.0/(delddelu)

	delr = delrdelu*deludeld

	print "delr = ",delr,'\n\n'

	return delr

def calc_photo_residual_uncertainty(u,frame,cur_keyframe,T):
	'''
	Calculates the photometric residual uncertainty

	Arguments:
		u: High gradient pixel location
		frame: Current frame as a numpy array
		cur_keyframe: Previous keyframe as a Keyframe object
		T: Estimated pose

	Returns:
		sigma: Residual uncertainty
	'''
	deriv = delr_delD(u,frame,cur_keyframe,T)
	sigma = (sigma_p**2 + (deriv**2)*cur_keyframe.U[u[0]][u[1]])**0.5
	print ("deriv = ",deriv)
	print ("sigma = ",sigma,'\n\n')
	return sigma

def huber_norm(x):
	'''
	Calculates and Returns the huber norm

	Arguments:
		x: Input

	Returns:
		Huber norm of x
	'''
	delta = 1 #Change later
	if abs(x)<delta:
		return 0.5*(x**2)
	else:
		return delta*(abs(x) - (delta/2))

def calc_cost(uu,frame,cur_keyframe,T):
	'''
	Calculates the residual error.

	Arguments:
		uu: An array containing the high gradient elements (X,2)
		frame: Numpy array o the current frame
		cur_keyframe: Previous keyframe as a Keyframe class
		pose: Current estimated Pose

	Returns:
		r: Residual error as a list
	'''

	return [huber_norm(calc_photo_residual(u,frame,cur_keyframe,T)/calc_photo_residual_uncertainty(u,frame,cur_keyframe,T)) for u in uu]

def calc_cost_jacobian(u,frame,cur_keyframe,T_s): 
	'''
	Calculates the residual error for the Jacobian

	Arguments:
		u: A list containing the high gradient elements
		frame: Numpy array o the current frame
		cur_keyframe: Previous keyframe as a Keyframe class
		T_s: Current estimated Pose as a flattened numpy array

	Returns:
		r: Residual error as a list
	'''
	T = _get_back_T(T_s)
	r = np.zeros(len(u))
	j = 0 #Count variable
	for i in u:
		r[j] = huber_norm(calc_photo_residual(i,frame,cur_keyframe,T)/calc_photo_residual_uncertainty(i,frame,cur_keyframe,T))
		j = j+1
	return r

def test(T):
	return (T**2)[:5]

def get_jacobian(dof,u,frame,cur_keyframe,T):
	'''
	Returns the Jacobian of the Residual Error wrt the Pose (r wrt T)

	r == (dof,1)
	T == (6,1)

	delr/delT == (dof,6)
	delr/delu == (dof,1)
	delT/delu == (6,1)
	delu/delT == (1,6) 
	delr/delT = (delr/delu)*(delu/delT)

	Arguments:
		dof: Number of high gradient elements we are using
		u: An array containing the high gradient elements
		frame: Numpy array o the current frame
		cur_keyframe: Previous keyframe as a Keyframe class
		T: Current estimated Pose

	Returns:
		J: The required Jacobian
	'''
	T_s = get_min_rep(T)
	T_c = tf.constant(T_s) #Flattened pose in tf
	r_s = tf.constant(calc_cost_jacobian(u,frame, cur_keyframe,T_c))
	#r_s = test(T_c)

	with tf.Session() as sess:
		print ("\n\nr_s = ",sess.run(r_s))
		print ("T_c = ",sess.run(T_c))
		print "T_s = ",T_s,'\n\n'

		#Following works with custom test function
		J1,J2 = tf.test.compute_gradient(T_c,(6,),r_s,(dof,),T_s) #Returns two jacobians... (Other parameters are the shapes and the initial values)
		print ('\n\n\n',J1,'\n\n\n')
		return J1.transpose() #6xdof #Returning jacobian transpose????

def get_W(dof,stack_r):
	'''
	Returns the weight matrix for weighted Gauss-Newton Optimization

	Arguments:
		dof: Number of high gradient elements we are using
		stack_r: The stacked residual error as a numpy array (of length dof)

	Returns:
		W: Weight Matrix
	'''
	W = np.random.random((dof,dof))
	for i in range(dof): 
		W[i][i] = (dof + 1)/(dof + stack_r[i]**2)
	return W

def exit_crit(delT):
	'''
	Checks for when to exit the loop while doing Gauss - Newton Optimization
	
	Arguments: 
		delT: The right multiplied increment of the pose

	Returns:
		1(to exit) or 0(not to exit)
	'''
	#Change later
	# TO DO
	return 1 

def minimize_cost_func(u,frame, cur_keyframe):
	'''
	Does Weighted Gauss-Newton Optimization

	Arguments:
		uu: List of points in high gradient areas of the current frame
		frame: Current frame(as a Numpy array)
		cur_keyframe: The previous keyframe of the Keyframe Class

	Returns:
		T: The camera Pose
	'''
	dof = len(u)

	# Random pose
	T_s = np.random.random((6))
	# So that the rotation matrix is valid
	T = _get_back_T(T_s)

	while True:
		stack_r = calc_cost(u,frame,cur_keyframe,T)
		
		J = get_jacobian(dof,u,frame,cur_keyframe,T) #dofx6
		Jt = J.transpose() #6xdof
		W = get_W(dof,stack_r) #dof x dof - diagonal matrix
		temp = np.matmul(np.matmul(Jt,W),J)
		print ("temp = ",temp)
		hess = np.linalg.inv(temp) # 12x12
		delT = np.matmul(hess,Jt)
		delT = np.matmul(delT,W)
		T_s = get_min_rep(T)
		delT = -np.matmul(delT,stack_r)
		#T = np.matmul(delT.transpose(),T_s) #Or do subtraction?
		
		for i in range(0,6):
			T_s[i] = T_s[i]*delT[i]

		T = _get_back_T(T_s)
		T = np.ones((3,4))
		if exit_crit(delT):
			break
	return T

def check_keyframe(T):
	'''
	Checks the Pose of a new frame to see if it is a keyframe(if the camera has moved too far from the previous keyframe)

	Arguments: 
		T: Pose of new frame wrt to prev keyframe

	Returns:
		Either 1(is a keyframe) or 0(not a keyframe)
	'''
	W = np.ones((6,6)) #Weight Matrix - change later
	threshold = 0
	T_s = get_min_rep(T)	
	temp = matmul(W,T_s) # 6x1
	temp = matmul(T_s.transpose(),temp)
	return temp>=threshold

def _delay():
	'''
	Adds a time delay
	'''
	time.sleep(60) #Change later

def _exit_program():
	'''
	Exits the program
	'''
	sys.exit(0)

def test_highgrad():
	'''
	Test thresholding based extraction of high gradient element.

	Laplace filter used
	'''
	im_x,im_y=im_size
	dummy_image=np.uint8(np.random.random((im_x,im_y,3))*256)
	dummy_image_grey=np.uint8((dummy_image[:,:,0]+dummy_image[:,:,1]+dummy_image[:,:,2])/3)

	# Test high  grad
	result=get_highgrad_element(dummy_image_grey,threshold=200)
	print("Testing high grad {} ".format(result))

	assert(result.shape[1]==2)

def test_min_cost_func():
	'''
	Test minimum cost function:

	* Take current frame, keyframe
	'''
	# Image Size
	im_x,im_y = im_size

	# Random high grad points
	u_test = np.array([[5,4],[34,56],[231,67],[100,100],[340,237]])

	# Random frame
	frame_test = np.uint8(np.random.random((im_x,im_y))*256)

	# Current key frame, depth, pose, uncertainuty
	cur_key_test_im = np.uint8(np.random.random((im_x,im_y,3))*256)
	cur_key_test_im_grey = np.uint8((cur_key_test_im[:,:,0]+cur_key_test_im[:,:,1]+cur_key_test_im[:,:,2])/3)
	cur_key_depth = np.random.random((im_x,im_y))
	dummy_pose=np.eye(4)[:3]
	cur_key_unc = np.ones((im_x,im_y))

	cur_key = Keyframe(dummy_pose,cur_key_depth,cur_key_unc,cur_key_test_im_grey)

	print("Testing minimize cost func",minimize_cost_func(u_test,frame_test,cur_key))

def test_get_min_rep():
	T = np.array([[0.36,0.48,-0.8,5],[-0.8,0.6,0,3],[0.48,0.64,0.60,8]])
	#T = np.array([[1,0,0,5],[0,math.sqrt(3)/2,0.5,3],[0,-0.5,math.sqrt(3)/2,8]]) # 30 degree rotation about x axis - works
	print (T,'\n')
	print ("Testing get_min_rep",get_min_rep(T))
	return get_min_rep(T)

def test_get_back_T():
	T_s = test_get_min_rep()
	print (T_s,'\n')
	print("Testing get_back_T",_get_back_T(T_s))

if __name__=='__main__':
	test_highgrad()
	test_min_cost_func()