#!/usr/bin/python
import os, rospy, roslib, select, sys, tf, time, math, qp_matrix, quadprog, npyscreen, threading

from std_msgs.msg import Header, Float32, Float64, Empty
from geometry_msgs.msg import PoseStamped, TwistStamped, Vector3, Quaternion, Point, Twist, PointStamped
from sensor_msgs.msg import BatteryState
from nav_msgs.msg import Path, Odometry
from visualization_msgs.msg import Marker
from gazebo_msgs.msg import ModelStates, ModelState
import numpy as np
from numpy import array
from qp_matrix import qp_q_dot_des_array, qp_non_holonomic_solver
from MPC_obstacle_avoidance import MPC_solver
from tf.transformations import euler_from_quaternion
from qp_planner.msg import algomsg
from qp_planner.msg import Obstacles

x_current = y_current = current_yaw                 = 0.0
x_destination = y_destination                       = 0.0
destination_yaw                                     = 0.1
x_velocity_des = y_velocity_des                     = 0.0 
final_pose                                          = [0.01, 0.01, 0.01]
init_pose                                           = [0, 0, 0]
current_pose                                        = [0, 0, 0]
y_home =  y_homeyaw                                 = 0
x_home                                              = 0
x_limit = y_limit                                   = 200
yaw                                                 = 0
y_homeyaw_recorded                                  = False
n                                                   = 30
t                                                   = .2
br                                                  = tf.TransformBroadcaster()
br2                                                 = tf.TransformBroadcaster()
pub6                                                = rospy.Publisher('/gazebo/set_model_state', ModelState, queue_size = 1)
cached_var                                          = {}
lin_vel_lim                                         = .15
ang_vel_lim                                         = .2

x_obs = y_obs = r_obs                               = [0.0]
vx_obs                                              = [0.0]
vy_obs                                              = [0.0]
obstacles                                           = []
r_vehicle                                           = 0.

batt_low                                            = False
is_simulation                                       = 1              #-1 for real, 1 for simulation

counter                                             = 0
max_time                                            = 0.
min_time                                            = 1000.
total_time                                          = 0.

theta_destination                                   = 0.1
global main_thread, debug, debug2, x_velocity_des, y_velocity_des

class form_object(npyscreen.Form):
    def create(self):
        global nsteps_slider, interval_slider, debug, debug2
        nsteps_slider = self.add(npyscreen.TitleSlider, name = "No. of steps:", value = 20, out_of = 50)
        interval_slider = self.add(npyscreen.TitleSlider, name = "Time interval:", value = .2, out_of = 10, step = 0.1)
        debug = self.add(npyscreen.TitleText)
        debug2 = self.add(npyscreen.TitleText)

    def afterEditing(self):
        global main_thread
        self.parentApp.setNextForm(None)
        main_thread.do_run = False
        main_thread.join()

class App(npyscreen.NPSAppManaged):
    def onStart(self):
        self.addForm('MAIN', form_object, name = "RBCCPS ADVANCED MPC CONTROLLER v2.646.23.1")

#Odometry data callback
def odom_cb(data):
    global current_yaw, x_current, y_current, current_pose

    x_current = data.pose.pose.position.x
    y_current = data.pose.pose.position.y
    orientation_q = data.pose.pose.orientation
    orientation_list = (orientation_q.x, orientation_q.y, orientation_q.z, orientation_q.w)
    (roll, pitch, current_yaw) = euler_from_quaternion(orientation_list)
    (roll, pitch, current_yaw) = (0, 0, math.degrees(current_yaw))

#Callback for setting destination for the solver
def calc_target_cb(data):
    global x_destination, y_destination, theta_destination, final_pose
    x_destination = x_home + data.pose.position.x
    y_destination = y_home + data.pose.position.y
    orientation_q = data.pose.orientation
    orientation_list = (orientation_q.x, orientation_q.y, orientation_q.z, orientation_q.w)
    (roll, pitch, theta_destination) = euler_from_quaternion(orientation_list)
    final_pose = [x_destination,  y_destination, theta_destination]

#To check for battery voltage of the Turtlebot
def batt_voltage_cb(data):
    
    if data.voltage < 10.0:
        print("Battery critical")
        batt_low = True

    elif(data.voltage < 11.0):
        print("Battery low")

#Receives obstacles info(x, y, radius) from the topic
def obstacles_cb(data):
    global x_obs, y_obs, r_obs, obstacles, x_velocity_des, y_velocity_des

    if(data != None):
        x_obs = np.array([obj.center.x for obj in data.circles])
        y_obs = np.array([obj.center.y for obj in data.circles])
        # Using + sign for subtraction because desired velocities are negated
        # vx_obs = np.array([obj.velocity.x + (0 if x_velocity_des < 0.1 else x_velocity_des) for obj in data.circles])
        # vy_obs = np.array([obj.velocity.y + (0 if y_velocity_des < 0.1 else y_velocity_des) for obj in data.circles])
        vx_obs = np.array([obj.velocity.x + x_velocity_des for obj in data.circles])
        vy_obs = np.array([obj.velocity.y + y_velocity_des for obj in data.circles])

        r_obs = np.array([obj.radius for obj in data.circles])
        # if vx_obs[0] > 0:
        #     vx_obs[0] = 0.5

        # else:
        #     vx_obs[0] = -0.5
        obstacles = [x_obs, y_obs, r_obs, vx_obs, vy_obs]


def main():
    global y_homeyaw, y_homeyaw_recorded, x_home, y_home, x_current, y_current, current_yaw, n, t, cached_var, ang_vel_lim, lin_vel_lim, theta_destination
    global x_limit, y_limit, obstacles, max_time, min_time, counter, total_time, main_thread, x_velocity_des, y_velocity_des, debug, debug2, final_pose

    xAnt = yAnt = 0
    rate = rospy.Rate(10.0)

    #Lidar odometry
    # rospy.Subscriber("/odom_rf2o", Odometry, odom_cb)
    #Wheel encoder odometry
    rospy.Subscriber("/odom", Odometry, odom_cb)

    rospy.Subscriber("/move_base_simple/goal", PoseStamped, calc_target_cb)
    rospy.Subscriber("/battery_state", BatteryState, batt_voltage_cb)
    rospy.Subscriber("/obstacles", Obstacles, obstacles_cb)
    # rospy.Subscriber("/gazebo/model_states", ModelStates, model_state_cb)

    pub = rospy.Publisher('destination_point', PointStamped, queue_size = 1)
    pub2 = rospy.Publisher('cmd_vel', Twist, queue_size = 5)
    pub3 = rospy.Publisher('boundary_cube', Marker, queue_size = 1)
    pub4 = rospy.Publisher('mpc_path', Path, queue_size=1)
    pub5 = rospy.Publisher('turtle_point', PointStamped, queue_size = 1)
    pub7 = rospy.Publisher('predicted_path', Path, queue_size = 1)
    pub8 = rospy.Publisher('v', Float64, queue_size = 5)
    pub9 = rospy.Publisher('omega', Float64, queue_size = 5)

    mpc_path = Path()
    obs_path = Path()

    main_thread = threading.currentThread()

    while getattr(main_thread, "do_run", True) and not rospy.is_shutdown():
        if(len(sys.argv) > 1):
            if(sys.argv[1] == "true"):
                n = int(nsteps_slider.value)
                t = interval_slider.value
        # debug.name = ""

        if y_homeyaw_recorded is False and current_yaw != 0:
            y_homeyaw = current_yaw
            y_homeyaw_recorded = True

        # current_r = math.sqrt(x_current * x_current + y_current * y_current)
        destination_r = math.sqrt(math.pow(x_destination - x_current, 2) + math.pow(y_destination - y_current, 2))
        # limit_r = math.sqrt(x_limit * x_limit + y_limit * y_limit)

        # this is for controlling the turtle bot, mpc solver only yields paths in cartesian space.
        # dx = x_destination - x_current
        # dy = y_destination - y_current
        # current_pose = [dx, dy, (current_yaw)%360]
        current_pose = [x_current, y_current, current_yaw]

        # timer = time.time()
        #Calls to the MPC solver
        # try:
        #     x_velocity_des, y_velocity_des, cached_var = MPC_solver(init_pose, current_pose, final_pose, nsteps=n, interval=t, variables=cached_var, r_vehicle=r_vehicle, obstacles=obstacles, x_vel_limit = lin_vel_lim, y_vel_limit = lin_vel_lim)

        # except ValueError:
        #     x_velocity_des = 0
        #     y_velocity_des = 0

        # if(len(x_obs)>0):

        # x_velocity_des = v * math.cos(math.radians(current_yaw))
        # y_velocity_des = v * math.sin(math.radians(current_yaw))
        
        # current_time = time.time() - timer

        # if(current_time > max_time):
        #     max_time = current_time

        # if(current_time < min_time):
        #     min_time = current_time

        # total_time += current_time
        # counter = counter + 1
        # avg_time = total_time / counter

        # if(counter > 100000):
        #     total_time = 0.
        #     counter = 0
        # print "Average time = %f \t Max time = %f \t Min time = %f" % (avg_time, max_time, min_time)
        # print(time.time() - timer)
            
        # x_arr = cached_var.get("solution")[1:n+1]
        # y_arr = cached_var.get("solution")[2 * n + 2:2 * n + 1 + n + 1]

        # x_velocity_des = np.clip(x_velocity_des, -lin_vel_lim, lin_vel_lim)
        # y_velocity_des = np.clip(y_velocity_des, -lin_vel_lim, lin_vel_lim)

        # x_e = x_arr[1] - x_arr[0]
        # y_e = y_arr[1] - y_arr[0]

        # destination_yaw  = math.atan2(y_e, x_e) * 180 / 3.1416
        # destination_yaw  = math.atan2(dy, dx) * 180 / 3.1416


        # current_yaw = 360.0 + current_yaw if current_yaw < 0 else current_yaw
        # destination_yaw = 360.0 + destination_yaw if destination_yaw < 0 else destination_yaw

        #This part controls the yaw of the turtlebot, taking into account the shortest direction for the desired yaw
        # if(math.fabs(destination_yaw - current_yaw) > 180):
        #     if(destination_yaw > current_yaw):
        #         temp_desired_yaw = destination_yaw - current_yaw
        #         temp_current_yaw = 361

        #     elif current_yaw > destination_yaw:
        #         temp_current_yaw = current_yaw - destination_yaw
        #         temp_desired_yaw = 361

        #     velocity_yaw_des = np.clip(temp_desired_yaw - temp_current_yaw, -ang_vel_lim, ang_vel_lim); 

        # else:
        #     velocity_yaw_des = np.clip(destination_yaw - current_yaw, -ang_vel_lim, ang_vel_lim);
        
        # final_pose[2] = math.degrees(math.atan2(final_pose[1] - current_pose[1], final_pose[0] - current_pose[0]))
        v, omega = qp_non_holonomic_solver(current_pose, final_pose, [x_obs[0], y_obs[0], r_obs[0]], 1.5, 1.5)
        pub8.publish(Float64(v))
        pub9.publish(Float64(omega))
        v = np.clip(v, -.2, .2)
        omega = np.clip(omega, -1, 1)

        # print(current_pose[0], current_pose[1], current_pose[2], final_pose[0], final_pose[1], final_pose[2], v, omega)

        move_cmd = Twist()
        if(destination_r < 0.1):
            move_cmd.linear.x = 0
            move_cmd.angular.z = 0

        # elif(math.fabs(destination_yaw - ((current_yaw) % 360)) > 20):
        #     move_cmd.linear.x = 0
        #     # move_cmd.angular.z = is_simulation * velocity_yaw_des * 5
        #     move_cmd.angular.z = is_simulation * omega

        else:
            # move_cmd.linear.x = math.sqrt(x_velocity_des**2 + y_velocity_des**2)
            move_cmd.linear.x = v
            # move_cmd.angular.z = is_simulation * velocity_yaw_des
            move_cmd.angular.z = is_simulation * omega

        pub2.publish(move_cmd)

        #From hereon, deals with visualization in RViz
        destination_point = PointStamped(header=Header(stamp=rospy.get_rostime()))
        destination_point.header.frame_id = 'local_origin'
        destination_point.point.x = x_destination - x_home
        destination_point.point.y = y_destination - y_home
        destination_point.point.z = 0
        pub.publish(destination_point)

        turtle_point = PointStamped(header=Header(stamp=rospy.get_rostime()))
        turtle_point.header.frame_id = 'local_origin'
        turtle_point.point.x = x_current 
        turtle_point.point.y = y_current
        turtle_point.point.z = 0
        pub5.publish(turtle_point)

        boundary_cube = Marker()
        boundary_cube.header.frame_id = 'local_origin'
        boundary_cube.header.stamp = rospy.Time.now()
        boundary_cube.action = boundary_cube.ADD
        boundary_cube.type = boundary_cube.CUBE
        boundary_cube.id = 0
        boundary_cube.scale.x = x_limit*2
        boundary_cube.scale.y = y_limit*2
        boundary_cube.scale.z = 1       
        boundary_cube.color.a = 0.5 
        boundary_cube.color.r = 0.4
        boundary_cube.color.g = 0.2
        boundary_cube.color.b = 0
        pub3.publish(boundary_cube)

        # mpc_pose_array = [None] * n
        # for i in range(0, n):
        #     mpc_pose = PoseStamped()
        #     mpc_pose.header.seq = i
        #     mpc_pose.header.stamp = rospy.Time.now() + rospy.Duration(t * i)
        #     mpc_pose.header.frame_id = "local_origin"
        #     mpc_pose.pose.position.x = - x_arr[i] + x_destination
        #     mpc_pose.pose.position.y = - y_arr[i] + y_destination
        #     mpc_pose_array[i] = mpc_pose

        # if (xAnt != mpc_pose.pose.position.x and yAnt != mpc_pose.pose.position.y):
        #     mpc_path.header.frame_id = "local_origin"
        #     mpc_path.header.stamp = rospy.Time.now()
        #     mpc_path.poses = mpc_pose_array

        # xAnt = mpc_pose.pose.orientation.x
        # yAnt = mpc_pose.pose.position.y

        # pub4.publish(mpc_path)

        if len(obstacles) > 0 and len(obstacles[0]) > 0:
            obs_pose_array = [None] * 2

            for i in range(0, 2):
                obs_pose = PoseStamped()
                obs_pose.header.seq = i
                obs_pose.header.stamp = rospy.Time.now() + rospy.Duration(t * n * i)
                obs_pose.header.frame_id = "local_origin"
                
                obs_pose.pose.position.x = obstacles[0][0] + (obstacles[3][0] * n * t * i)
                obs_pose.pose.position.y = obstacles[1][0] + (obstacles[4][0] * n * t * i)
                obs_pose_array[i] = obs_pose

            obs_path.header.frame_id = "local_origin"
            obs_path.header.stamp = rospy.Time.now()
            obs_path.poses = obs_pose_array

            pub7.publish(obs_path)

        br.sendTransform((0, 0, 0), (0, 0, 0, 1), rospy.Time.now(), "odom", "local_origin")
        # br2.sendTransform((0, 0, 0), (0, 0, 0,1), rospy.Time.now(), "odom", "map")
        br2.sendTransform((0, 0, 0), (0, 0, 0, 1), rospy.Time.now(), "odom", "base_scan")


if __name__ == "__main__":
    global main_thread
    np.set_printoptions(precision=None, threshold=None, edgeitems=None, linewidth=1000, suppress=None, nanstr=None, infstr=None, formatter=None)
    rospy.init_node("Turtle_control")

    if(len(sys.argv) > 1):
        if(sys.argv[1] == "true"):
            do_run = True
            app = App().run()
            main_thread = threading.Thread(target = main)
            main_thread.start()

    else:
        main()