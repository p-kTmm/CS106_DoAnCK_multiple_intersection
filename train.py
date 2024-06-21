from __future__ import absolute_import
from __future__ import print_function

import os
import sys
import time
import optparse
import random
import serial
import numpy as np
import torch
import torch.optim as optim
import torch.nn.functional as F
import torch.nn as nn
import matplotlib.pyplot as plt

# we need to import python modules from the $SUMO_HOME/tools directory
if "SUMO_HOME" in os.environ:
    tools = os.path.join(os.environ["SUMO_HOME"], "tools")
    sys.path.append(tools)
else:
    sys.exit("please declare environment variable 'SUMO_HOME'")

from sumolib import checkBinary  # noqa
import traci  # noqa

def get_vehicle_numbers(lanes):
    vehicle_per_lane = dict()
    for l in lanes:
        vehicle_per_lane[l] = 0
        for k in traci.lane.getLastStepVehicleIDs(l):
            if traci.vehicle.getLanePosition(k) > 10:
                vehicle_per_lane[l] += 1
    return vehicle_per_lane


def get_waiting_time(lanes):
    waiting_time = 0
    for lane in lanes:
        waiting_time += traci.lane.getWaitingTime(lane)
    return waiting_time

# def get_waiting_time(lanes):
#     waiting_time = 0
#     for lane in lanes:
#         vehicles = traci.lane.getLastStepVehicleIDs(lane)
#         for vehicle in vehicles:
#             waiting_time += 2*traci.vehicle.getAccumulatedWaitingTime(vehicle) 
#             """
#             vì có nhiều giao lộ, nên nếu 1 xe ở 1 giao lộ thì waiting time của nó là rất nhỏ, 
#             ko đủ đề model quyết định bật đèn xanh, do đó hệ số này sẽ giúp tăng trọng số của waiting time
#             """
#     return waiting_time


def phaseDuration(junction, phase_time, phase_state):
    traci.trafficlight.setRedYellowGreenState(junction, phase_state)
    traci.trafficlight.setPhaseDuration(junction, phase_time)


class Model(nn.Module):
    def __init__(self, lr, input_dims, fc1_dims, fc2_dims, n_actions):
        super(Model, self).__init__()
        self.lr = lr
        self.input_dims = input_dims
        self.fc1_dims = fc1_dims
        self.fc2_dims = fc2_dims
        self.n_actions = n_actions

        self.linear1 = nn.Linear(self.input_dims, self.fc1_dims)
        self.linear2 = nn.Linear(self.fc1_dims, self.fc2_dims)
        self.linear3 = nn.Linear(self.fc2_dims, self.n_actions)

        self.optimizer = optim.Adam(self.parameters(), lr=self.lr)
        self.loss = nn.MSELoss()
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.to(self.device)

    def forward(self, state):
        x = F.relu(self.linear1(state))
        x = F.relu(self.linear2(x))
        actions = self.linear3(x)
        return actions


class Agent:
    def __init__(
        self,
        gamma,
        epsilon,
        lr,
        input_dims,
        fc1_dims,
        fc2_dims,
        batch_size,
        n_actions,
        junctions,
        model_type='dqn',  # thêm tham số này
        max_memory_size=100000,
        epsilon_dec=5e-4,
        epsilon_end=0.05,
    ):
        self.gamma = gamma
        self.epsilon = epsilon
        self.lr = lr
        self.batch_size = batch_size
        self.input_dims = input_dims
        self.fc1_dims = fc1_dims
        self.fc2_dims = fc2_dims
        self.n_actions = n_actions
        self.action_space = [i for i in range(n_actions)]
        self.junctions = junctions
        self.max_mem = max_memory_size
        self.epsilon_dec = epsilon_dec
        self.epsilon_end = epsilon_end
        self.mem_cntr = 0
        self.iter_cntr = 0
        self.replace_target = 5
        self.model_type = model_type  # thêm tham số này

        self.Q_eval = Model(
            self.lr, self.input_dims, self.fc1_dims, self.fc2_dims, self.n_actions
        )
        
        if self.model_type == 'ddqn':
            self.Q_target = Model(
                self.lr, self.input_dims, self.fc1_dims, self.fc2_dims, self.n_actions
            )
            self.Q_target.load_state_dict(self.Q_eval.state_dict())

        self.memory = dict()
        for junction in junctions:
            self.memory[junction] = {
                "state_memory": np.zeros(
                    (self.max_mem, self.input_dims), dtype=np.float32
                ),
                "new_state_memory": np.zeros(
                    (self.max_mem, self.input_dims), dtype=np.float32
                ),
                "reward_memory": np.zeros(self.max_mem, dtype=np.float32),
                "action_memory": np.zeros(self.max_mem, dtype=np.int32),
                "terminal_memory": np.zeros(self.max_mem, dtype=np.bool_),
                "mem_cntr": 0,
                "iter_cntr": 0,
            }

    def store_transition(self, state, state_, action, reward, done, junction):
        index = self.memory[junction]["mem_cntr"] % self.max_mem
        self.memory[junction]["state_memory"][index] = state
        self.memory[junction]["new_state_memory"][index] = state_
        self.memory[junction]['reward_memory'][index] = reward
        self.memory[junction]['terminal_memory'][index] = done
        self.memory[junction]["action_memory"][index] = action
        self.memory[junction]["mem_cntr"] += 1

    # def choose_action(self, observation):
    #     state = torch.tensor([observation], dtype=torch.float).to(self.Q_eval.device)
    #     if np.random.random() > self.epsilon:
    #         actions = self.Q_eval.forward(state)
    #         action = torch.argmax(actions).item()
    #     else:
    #         action = np.random.choice(self.action_space)
    #     return action
    def choose_action(self, observation):
        state = torch.tensor([observation], dtype=torch.float).to(self.Q_eval.device)
        if np.random.random() > self.epsilon:
            if self.model_type == 'ddqn':
                actions = self.Q_target.forward(state)
            elif self.model_type == 'dqn':
                actions = self.Q_eval.forward(state)
            action = torch.argmax(actions).item()
        else:
            action = np.random.choice(self.action_space)
        return action

    def reset(self, junction_numbers):
        for junction_number in junction_numbers:
            self.memory[junction_number]['mem_cntr'] = 0

    # def save(self, model_name):
    #     torch.save(self.Q_eval.state_dict(), f'models/{model_name}.bin')
    def save(self, model_name):
        if self.model_type == 'ddqn':
            torch.save(self.Q_target.state_dict(), f'models/{model_name}.bin')
        elif self.model_type == 'dqn':
            torch.save(self.Q_eval.state_dict(), f'models/{model_name}.bin')

    def learn(self, junction):
        if self.memory[junction]['mem_cntr'] < self.batch_size:
            return
    
        self.Q_eval.optimizer.zero_grad()
    
        max_mem = min(self.memory[junction]['mem_cntr'], self.max_mem)
        batch_indices = np.random.choice(max_mem, self.batch_size, replace=False)
    
        state_batch = torch.tensor(self.memory[junction]["state_memory"][batch_indices]).to(
            self.Q_eval.device
        )
        new_state_batch = torch.tensor(
            self.memory[junction]["new_state_memory"][batch_indices]
        ).to(self.Q_eval.device)
        reward_batch = torch.tensor(
            self.memory[junction]['reward_memory'][batch_indices]).to(self.Q_eval.device)
        terminal_batch = torch.tensor(self.memory[junction]['terminal_memory'][batch_indices]).to(self.Q_eval.device)
        action_batch = torch.tensor(self.memory[junction]["action_memory"][batch_indices], dtype=torch.int64).to(self.Q_eval.device)
    
        q_eval = self.Q_eval.forward(state_batch).gather(1, action_batch.unsqueeze(-1)).squeeze(-1)
    
        if self.model_type == 'ddqn':
            q_next = self.Q_target.forward(new_state_batch)
            q_eval_next = self.Q_eval.forward(new_state_batch)
            max_actions = torch.argmax(q_eval_next, dim=1)
            q_next[terminal_batch] = 0.0
            q_target = reward_batch + self.gamma * q_next.gather(1, max_actions.unsqueeze(-1)).squeeze(-1)
        else:
            q_next = self.Q_eval.forward(new_state_batch)
            q_next[terminal_batch] = 0.0
            q_target = reward_batch + self.gamma * torch.max(q_next, dim=1)[0]
    
        loss = self.Q_eval.loss(q_target, q_eval).to(self.Q_eval.device)
    
        loss.backward()
        self.Q_eval.optimizer.step()
    
        self.iter_cntr += 1
        self.epsilon = (
            self.epsilon - self.epsilon_dec
            if self.epsilon > self.epsilon_end
            else self.epsilon_end
        )
    
        if self.model_type == 'ddqn' and self.iter_cntr % self.replace_target == 0:
            self.Q_target.load_state_dict(self.Q_eval.state_dict())



def run(train=True, model_name="model", epochs=50, steps=500, ard=False, model_type='dqn'):
    if ard:
        arduino = serial.Serial(port="/dev/cu.usbmodem101", baudrate=9600, timeout=.1)
        def write_read(x):
            arduino.write(bytes(x, 'utf-8'))
            time.sleep(0.05)
            data = arduino.readline()
            return data
    """execute the TraCI control loop"""
    epochs = epochs
    steps = steps
    best_time = np.inf
    total_time_list = list()
    traci.start(
        [checkBinary("sumo"), "-c", "configuration.sumocfg", "--tripinfo-output", "maps/tripinfo.xml"]
    )
    all_junctions = traci.trafficlight.getIDList()
    junction_numbers = list(range(len(all_junctions)))

    brain = Agent(
        gamma=0.99,
        epsilon=0.0,
        lr=0.1,
        input_dims=4,
        fc1_dims=256,
        fc2_dims=256,
        batch_size=1024,
        n_actions=4,
        junctions=junction_numbers,
        model_type=model_type,
    )

    if not train:
        brain.Q_eval.load_state_dict(torch.load(f'models/{model_name}.bin', map_location=brain.Q_eval.device))

    print(brain.Q_eval.device)
    traci.close()
    for e in range(epochs):
        if train:
            traci.start(
            [checkBinary("sumo"), "-c", "configuration.sumocfg", "--tripinfo-output", "tripinfo.xml"]
            )
        else:
            traci.start(
            # [checkBinary("sumo-gui"), "-c", "configuration.sumocfg", "--tripinfo-output", "tripinfo.xml"]
            [checkBinary("sumo"), "-c", "configuration.sumocfg", "--tripinfo-output", "tripinfo.xml"]
            )

        print(f"epoch: {e}")
        select_lane = [
            ["yyyrrrrrrrrr", "GGGrrrrrrrrr"],
            ["rrryyyrrrrrr", "rrrGGGrrrrrr"],
            ["rrrrrryyyrrr", "rrrrrrGGGrrr"],
            ["rrrrrrrrryyy", "rrrrrrrrrGGG"],
        ]

        step = 0
        total_time = 0
        min_duration = 5

        wt_per_step  =  0
        wt_list = []
        
        traffic_lights_time = dict()
        prev_wait_time = dict()
        prev_vehicles_per_lane = dict()
        prev_action = dict()
        all_lanes = list()
        
        for junction_number, junction in enumerate(all_junctions):
            prev_wait_time[junction] = 0
            prev_action[junction_number] = 0
            traffic_lights_time[junction] = 0
            prev_vehicles_per_lane[junction_number] = [0] * 4
            all_lanes.extend(list(traci.trafficlight.getControlledLanes(junction)))

        while step <= steps:
            traci.simulationStep()
            for junction_number, junction in enumerate(all_junctions):
                controled_lanes = traci.trafficlight.getControlledLanes(junction)
                waiting_time = get_waiting_time(controled_lanes)
                total_time += waiting_time
                wt_per_step += waiting_time

                if traffic_lights_time[junction] == 0:
                    vehicles_per_lane = get_vehicle_numbers(controled_lanes)

                    reward = -1 *  waiting_time
                    state_ = list(vehicles_per_lane.values()) 
                    state = prev_vehicles_per_lane[junction_number]
                    prev_vehicles_per_lane[junction_number] = state_
                    brain.store_transition(state, state_, prev_action[junction_number], reward, (step == steps), junction_number)

                    lane = brain.choose_action(state_)
                    prev_action[junction_number] = lane
                    phaseDuration(junction, 6, select_lane[lane][0])
                    phaseDuration(junction, min_duration + 10, select_lane[lane][1])

                    if ard:
                        ph = str(traci.trafficlight.getRedYellowGreenState("gneJ2"))
                        if ph == "GGGrrrrrrrrr":
                            ph = 0
                        elif ph == "rrrGGGrrrrrr":
                            ph = 2
                        elif ph == "rrrrrrGGGrrr":
                            ph = 4
                        elif ph == "rrrrrrrrrGGG":
                            ph = 6
                        value = write_read(str(ph))

                    traffic_lights_time[junction] = min_duration + 10
                    if train:
                        brain.learn(junction_number)
                else:
                    traffic_lights_time[junction] -= 1

            wt_list.append(wt_per_step)
            wt_per_step = 0
            step += 1

        print("total_time", total_time)
        total_time_list.append(total_time)

        if total_time < best_time:
            best_time = total_time
            if train:
                brain.save(model_name)

        traci.close()
        sys.stdout.flush()
        if not train:
            break

    if train:
        plt.plot(list(range(len(total_time_list))), total_time_list)
        plt.xlabel("epochs")
        plt.ylabel("total time")
        plt.savefig(f'plots_training/time_vs_epoch_{model_name}.png')

    else:
        model_dir = f"plots_testing/{model_name}"
        try:
            os.mkdir(model_dir)
        except OSError as error:
            pass
        with open(f'{model_dir}/waiting_time.txt', 'w') as f:
            for line in wt_list:
                f.write(f"{line}\n")
                
        plt.plot(list(range(len(wt_list))), wt_list)
        plt.xlabel("steps")
        plt.ylabel("total time")
        plt.savefig(f'{model_dir}/waiting_time.png')
        plt.show()


def get_options():
    optParser = optparse.OptionParser()
    optParser.add_option(
        "-m",
        dest='model_city1',
        type='string',
        default="model",
        help="name of model",
    )
    optParser.add_option(
        "--train",
        action='store_true',
        default=False,
        help="training or testing",
    )
    optParser.add_option(
        "-e",
        dest='epochs',
        type='int',
        default=50,
        help="Number of epochs",
    )
    optParser.add_option(
        "-s",
        dest='steps',
        type='int',
        default=500,
        help="Number of steps",
    )
    optParser.add_option(
        "--ard",
        action='store_true',
        default=False,
        help="Connect Arduino", 
    )
    optParser.add_option(
        "--option_model",
        dest='option_model',
        type='string',
        default='dqn',
        help="Model type: 'dqn' or 'ddqn'",
    )
    options, args = optParser.parse_args()
    return options


# this is the main entry point of this script
if __name__ == "__main__":
    options = get_options()
    model_name = options.model_city1
    train = options.train
    epochs = options.epochs
    steps = options.steps
    ard = options.ard
    model_type = options.option_model  # lấy tùy chọn model

    run(train=train, model_name=model_name, epochs=epochs, steps=steps, ard=ard, model_type=model_type)

