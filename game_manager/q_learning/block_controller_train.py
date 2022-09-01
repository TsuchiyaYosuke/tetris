#!/usr/bin/python3
# -*- coding: utf-8 -*-

from datetime import datetime
import pprint
import random
import copy
import torch
import torch.nn as nn
import sys
sys.path.append("game_manager/q_learning/")
import omegaconf
from hydra import compose, initialize
import os
from tensorboardX import SummaryWriter
from collections import deque
from random import random, sample,randint
import shutil
import glob 
import numpy as np
import yaml
import subprocess



class Block_Controller(object):
    board_backboard = 0
    board_data_width = 0
    board_data_height = 0
    ShapeNone_index = 0
    CurrentShape_class = 0
    NextShape_class = 0

    def __init__(self):
        self.mode = None
        self.init_train_parameter_flag = False
        self.init_predict_parameter_flag = False

    def yaml_read(self,yaml_file):
        with open(yaml_file) as f:
            config = yaml.safe_load(f)
        return config

    def set_parameter(self, yaml_file=None, predict_weight=None):
        self.result_depository = "outputs/"
        self.latest_dir = self.result_depository + "/latest"
        if self.mode=="train" or self.mode=="train_sample_qlearing" or self.mode=="train_sample2":
            dt = datetime.now()
            self.output_dir = self.result_depository + dt.strftime("%Y-%m-%d-%H-%M-%S")
            os.makedirs(self.output_dir, exist_ok=True)
            self.weight_dir = self.output_dir+"/trained_model/"
            self.best_weight = self.weight_dir + "best_weight.pt"
            os.makedirs(self.weight_dir, exist_ok=True)
        else:
            dirname = os.path.dirname(predict_weight)
            self.output_dir = dirname + "/predict/"
            os.makedirs(self.output_dir, exist_ok=True)

        if yaml_file is None:
            raise Exception('Input train_yaml file.')
        elif not os.path.exists(yaml_file):
            raise Exception('The yaml file {} is not existed.'.format(yaml_file))
        config = self.yaml_read(yaml_file)

        subprocess.run("cp config/default.yaml %s/"%(self.output_dir), shell=True)
        self.writer = SummaryWriter(self.output_dir+"/"+config["common"]["log_path"])

        if self.mode=="predict" or self.mode=="predict_sample_qlearning":
            self.log = self.output_dir + "/log_predict.txt"
            self.log_score = self.output_dir + "/score_predict.txt"
            self.log_reward = self.output_dir + "/reward_predict.txt"
        else:
            self.log = self.output_dir+"/log_train.txt"
            self.log_score = self.output_dir+"/score_train.txt"
            self.log_reward = self.output_dir+"/reward_train.txt"

        with open(self.log,"w") as f:
            print("Start", file=f)

        with open(self.log_score,"w") as f:
            print(0, file=f)

        with open(self.log_reward,"w") as f:
            print(0, file=f)

        self.height = config["tetris"]["board_height"]
        self.width = config["tetris"]["board_width"]
        self.max_tetrominoes = config["tetris"]["max_tetrominoes"]
        
        self.state_dim = config["state"]["dim"]
        print("model name: %s"%(config["model"]["name"]))
        if config["model"]["name"]=="MLP":
            from q_learning.model.deepqnet import MLP
            self.model = MLP(self.state_dim)
            self.initial_state = torch.FloatTensor([0 for i in range(self.state_dim)])
            self.get_next_func = self.get_next_states
            self.reward_func = self.step
        elif config["model"]["name"]=="DQN":
            from q_learning.model.deepqnet import DeepQNetwork as DQN
            self.model = DQN()
            self.initial_state = torch.FloatTensor([[[0 for i in range(10)] for j in range(22)]])
            self.get_next_func = self.get_next_states_v2
            self.reward_func = self.step_v2
            self.reward_weight = config["train"]["reward_weight"]

        if self.mode=="predict" or self.mode=="predict_sample_qlearning":
            if not predict_weight=="None":
                if os.path.exists(predict_weight):
                    print("Load {}...".format(predict_weight))
                    self.model = torch.load(predict_weight)
                    self.model.eval()    
                else:
                    print("{} is not existed".format(predict_weight))
                    exit()
            else:
                print("Please set predict_weight")
                exit()
        elif config["model"]["finetune"]:
            self.ft_weight = config["common"]["ft_weight"]
            if not self.ft_weight is None:
                self.model = torch.load(self.ft_weight)
                with open(self.log,"a") as f:
                    print("Finetuning mode\nLoad {}".format(self.ft_weight), file=f)
                
            
        if torch.cuda.is_available():
            self.model.cuda()
        
        self.batch_size = config["train"]["batch_size"]
        self.lr = config["train"]["lr"]
        if not isinstance(self.lr,float):
            self.lr = float(self.lr)

        self.replay_memory_size = config["train"]["replay_memory_size"]
        self.replay_memory = deque(maxlen=self.replay_memory_size)
        self.max_episode_size = self.max_tetrominoes
        self.episode_memory = deque(maxlen=self.max_episode_size)
        
        self.num_decay_epochs = config["train"]["num_decay_epochs"]
        self.num_epochs = config["train"]["num_epoch"]
        self.initial_epsilon = config["train"]["initial_epsilon"]
        self.final_epsilon = config["train"]["final_epsilon"]
        if not isinstance(self.final_epsilon,float):
            self.final_epsilon = float(self.final_epsilon)

        if config["train"]["optimizer"]=="Adam" or config["train"]["optimizer"]=="ADAM":
            self.optimizer = torch.optim.Adam(self.model.parameters(), lr=self.lr)
            self.scheduler = None
        else:
            self.momentum =config["train"]["lr_momentum"] 
            self.optimizer = torch.optim.SGD(self.model.parameters(), lr=self.lr, momentum=self.momentum)
            self.lr_step_size = config["train"]["lr_step_size"]
            self.lr_gamma = config["train"]["lr_gamma"]
            self.scheduler = torch.optim.lr_scheduler.StepLR(self.optimizer, step_size=self.lr_step_size , gamma=self.lr_gamma)
        self.criterion = nn.MSELoss()

        self.epoch = 0
        self.score = 0
        self.max_score = -100000
        self.epoch_reward = 0
        self.cleared_lines = 0
        self.iter = 0 
        self.state = self.initial_state 
        self.tetrominoes = 0
        
        self.gamma = config["train"]["gamma"]
        self.reward_clipping = config["train"]["reward_clipping"]

        self.score_list = config["tetris"]["score_list"]
        self.reward_list = config["train"]["reward_list"]
        self.penalty =  self.reward_list[5]
        
        if self.reward_clipping:
            self.norm_num =max(max(self.reward_list),abs(self.penalty))            
            self.reward_list =[r/self.norm_num for r in self.reward_list]
            self.penalty /= self.norm_num
            self.penalty = min(config["train"]["max_penalty"],self.penalty)

        self.double_dqn = config["train"]["double_dqn"]
        self.target_net = config["train"]["target_net"]
        if self.double_dqn:
            self.target_net = True
            
        if self.target_net:
            print("set target network...")
            self.target_model = copy.deepcopy(self.model)
            self.target_copy_intarval = config["train"]["target_copy_intarval"]
        self.prioritized_replay = config["train"]["prioritized_replay"]
        if self.prioritized_replay:
            from q_learning.qlearning import PRIORITIZED_EXPERIENCE_REPLAY as PER
            self.PER = PER(self.replay_memory_size, gamma=self.gamma)
        
        self.multi_step_learning = config["train"]["multi_step_learning"]
        if self.multi_step_learning:
            from q_learning.qlearning import Multi_Step_Learning as MSL
            self.multi_step_num = config["train"]["multi_step_num"]
            self.MSL = MSL(step_num=self.multi_step_num, gamma=self.gamma)
    def stack_replay_memory(self):
        if self.mode=="train" or self.mode=="train_sample_qlearing" or self.mode=="train_sample2":
            self.score += self.score_list[5]
            self.episode_memory[-1][1] += self.penalty
            self.episode_memory[-1][3] = True
            self.epoch_reward += self.penalty
            if self.multi_step_learning:
                self.episode_memory = self.MSL.arrange(self.episode_memory)
                
            self.replay_memory.extend(self.episode_memory)
            self.episode_memory = deque(maxlen=self.max_episode_size)
        else:
            pass
    
    def update(self):

        if self.mode=="train" or self.mode=="train_sample_qlearing" or self.mode=="train_sample2":
            self.stack_replay_memory()

            if len(self.replay_memory) < self.replay_memory_size / 10:
                print("iter: {} ,meory: {}/{} , score: {}, clear line: {}, block: {} ".format(self.iter,
                len(self.replay_memory),self.replay_memory_size / 10,self.score,self.cleared_lines
                ,self.tetrominoes ))
            else:
                self.epoch += 1
                if self.prioritized_replay:
                    batch,replay_batch_index = self.PER.sampling(self.replay_memory, self.batch_size)
                else:
                    batch = sample(self.replay_memory, min(len(self.replay_memory), self.batch_size))
                    

                state_batch, reward_batch, next_state_batch, done_batch = zip(*batch)
                state_batch = torch.stack(tuple(state for state in state_batch))
                reward_batch = torch.from_numpy(np.array(reward_batch, dtype=np.float32)[:, None])
                next_state_batch = torch.stack(tuple(state for state in next_state_batch))

                done_batch = torch.from_numpy(np.array(done_batch)[:, None])

                q_values = self.model(state_batch)
                

                if self.target_net:
                    if self.epoch %self.target_copy_intarval==0 and self.epoch>0:
                        print("Network update")
                        self.target_model = torch.load(self.best_weight)
                    self.target_model.eval()
                    with torch.no_grad():
                        next_prediction_batch = self.target_model(next_state_batch)
                else:
                    self.model.eval()
                    with torch.no_grad():
                        next_prediction_batch = self.model(next_state_batch)

                self.model.train()
                
                if self.multi_step_learning:
                    print("Multi step updating")
                    y_batch = self.MSL.get_y_batch(done_batch,reward_batch, next_prediction_batch)              
                else:
                    y_batch = torch.cat(
                        tuple(reward if done[0] else reward + self.gamma * prediction for done ,reward, prediction in
                            zip(done_batch,reward_batch, next_prediction_batch)))[:, None]
                
                self.optimizer.zero_grad()
                if self.prioritized_replay:
                    loss_weights = self.PER.update_priority(replay_batch_index, reward_batch, q_values, next_prediction_batch)
                    loss = (loss_weights *self.criterion(q_values, y_batch)).mean()
                    loss.backward()
                else:
                    loss = self.criterion(q_values, y_batch)
                    loss.backward()
                
                self.optimizer.step()
                
                if self.scheduler!=None:
                    self.scheduler.step()
                
                log = "epoch: {} / {}, score: {},  block: {},  reward: {:.1f} cleared lines: {}".format(
                    self.epoch,
                    self.num_epochs,
                    self.score,
                    self.tetrominoes,
                    self.epoch_reward,
                    self.cleared_lines
                    )
                print(log)
                with open(self.log,"a") as f:
                    print(log, file=f)
                with open(self.log_score,"a") as f:
                    print(self.score, file=f)

                with open(self.log_reward,"a") as f:
                    print(self.epoch_reward, file=f)
                    
                self.writer.add_scalar('Train/Score', self.score, self.epoch - 1) 
                self.writer.add_scalar('Train/Reward', self.epoch_reward, self.epoch - 1)   
                self.writer.add_scalar('Train/block', self.tetrominoes, self.epoch - 1)  
                self.writer.add_scalar('Train/clear lines', self.cleared_lines, self.epoch - 1) 
                    
            if self.epoch > self.num_epochs:
                with open(self.log,"a") as f:
                    print("finish..", file=f)
                if os.path.exists(self.latest_dir):
                    shutil.rmtree(self.latest_dir)
                os.makedirs(self.latest_dir,exist_ok=True)
                shutil.copyfile(self.best_weight, self.latest_dir + "/best_weight.pt")
                for file in glob.glob(self.output_dir+"/*.txt"):
                    shutil.copyfile(file, self.latest_dir + "/" + os.path.basename(file))
                for file in glob.glob(self.output_dir + "/*.yaml"):
                    shutil.copyfile(file, self.latest_dir + "/" + os.path.basename(file))
                with open(self.latest_dir + "/copy_base.txt","w") as f:
                    print(self.best_weight, file=f)
                exit() 
        else:
            self.epoch += 1
            log = "epoch: {} / {}, score: {},  block: {}, reward: {:.1f} , cleared lines: {}".format(
            self.epoch,
            self.num_epochs,
            self.score,
            self.tetrominoes,
            self.epoch_reward,
            self.cleared_lines
            )
        self.reset_state()
        
    def reset_state(self):
        if self.mode=="train" or self.mode=="train_sample_qlearing" or self.mode=="train_sample2": 
            if self.score > self.max_score:
                torch.save(self.model, "{}/tetris_epoch{}_score{}.pt".format(self.weight_dir,self.epoch,self.score))
                self.max_score  =  self.score
                torch.save(self.model,self.best_weight)
        self.state = self.initial_state
        self.score = 0
        self.cleared_lines = 0
        self.epoch_reward = 0
        self.tetrominoes = 0
    
    def check_cleared_rows(self,board):
        board_new = np.copy(board)
        lines = 0
        empty_line = np.array([0 for i in range(self.width)])
        for y in range(self.height - 1, -1, -1):
            blockCount  = np.sum(board[y])
            if blockCount == self.width:
                lines += 1
                board_new = np.delete(board_new,y,0)
                board_new = np.vstack([empty_line, board_new ])
        return lines, board_new

    def get_bumpiness_and_height(self,board):
        mask = board != 0
        invert_heights = np.where(mask.any(axis=0), np.argmax(mask, axis=0), self.height)
        heights = self.height - invert_heights
        total_height = np.sum(heights)
        currs = heights[:-1]
        nexts = heights[1:]
        diffs = np.abs(currs - nexts)
        total_bumpiness = np.sum(diffs)
        return total_bumpiness, total_height

    def get_holes(self, board):
        num_holes = 0
        for i in range(self.width):
            col = board[:,i]
            row = 0
            while row < self.height and col[row] == 0:
                row += 1
            num_holes += len([x for x in col[row + 1:] if x == 0])
        return num_holes

    def get_state_properties(self, board):
        lines_cleared, board = self.check_cleared_rows(board)
        holes = self.get_holes(board)
        bumpiness, height = self.get_bumpiness_and_height(board)

        return torch.FloatTensor([lines_cleared, holes, bumpiness, height])

    def get_state_properties_v2(self, board):
        lines_cleared, board = self.check_cleared_rows(board)
        holes = self.get_holes(board)
        bumpiness, height = self.get_bumpiness_and_height(board)
        max_row = self.get_max_height(board)
        return torch.FloatTensor([lines_cleared, holes, bumpiness, height,max_row])

    def get_max_height(self, board):
        sum_ = np.sum(board,axis=1)
        row = 0
        while row < self.height and sum_[row] ==0:
            row += 1
        return self.height - row

    def get_next_states_v2(self,curr_backboard,piece_id,CurrentShape_class):
        states = {}
        
        if piece_id == 5:
            num_rotations = 1
        elif piece_id == 1 or piece_id == 6 or piece_id == 7:
            num_rotations = 2
        else:
            num_rotations = 4

        for direction0 in range(num_rotations):
            x0Min, x0Max = self.getSearchXRange(CurrentShape_class, direction0)
            for x0 in range(x0Min, x0Max):
                board = self.getBoard(curr_backboard, CurrentShape_class, direction0, x0)
                reshape_backboard = self.get_reshape_backboard(board)
                reshape_backboard = torch.from_numpy(reshape_backboard[np.newaxis,:,:]).float()
                states[(x0, direction0)] = reshape_backboard
        return states

    def get_next_states(self, curr_backboard, piece_id, CurrentShape_class):
        states = {}
        if piece_id == 5:
            num_rotations = 1
        elif piece_id == 1 or piece_id == 6 or piece_id == 7:
            num_rotations = 2
        else:
            num_rotations = 4

        for direction0 in range(num_rotations):
            x0Min, x0Max = self.getSearchXRange(CurrentShape_class, direction0)
            for x0 in range(x0Min, x0Max):
                board = self.getBoard(curr_backboard, CurrentShape_class, direction0, x0)
                board = self.get_reshape_backboard(board)
                states[(x0, direction0)] = self.get_state_properties(board)
        return states

    def get_reshape_backboard(self,board):
        board = np.array(board)
        reshape_board = board.reshape(self.height,self.width)
        reshape_board = np.where(reshape_board>0,1,0)
        return reshape_board

    def step_v2(self, curr_backboard, action, curr_shape_class):
        x0, direction0 = action
        board = self.getBoard(curr_backboard, curr_shape_class, direction0, x0)
        board = self.get_reshape_backboard(board)
        bampiness,height = self.get_bumpiness_and_height(board)
        max_height = self.get_max_height(board)
        hole_num = self.get_holes(board)
        lines_cleared, board = self.check_cleared_rows(board)
        reward = self.reward_list[lines_cleared] 
        reward -= self.reward_weight[0] *bampiness 
        reward -= self.reward_weight[1] * max(0,max_height)
        reward -= self.reward_weight[2] * hole_num

        self.epoch_reward += reward 
        self.score += self.score_list[lines_cleared]
        self.cleared_lines += lines_cleared
        self.tetrominoes += 1
        return reward
    
    def step(self, curr_backboard, action, curr_shape_class):
        x0, direction0 = action
        board = self.getBoard(curr_backboard, curr_shape_class, direction0, x0)
        board = self.get_reshape_backboard(board)
        lines_cleared, board = self.check_cleared_rows(board)
        reward = self.reward_list[lines_cleared] 
        self.epoch_reward += reward
        self.score += self.score_list[lines_cleared]
        self.cleared_lines += lines_cleared
        self.tetrominoes += 1
        return reward
           
    def GetNextMove(self, nextMove, GameStatus,yaml_file=None,weight=None):

        t1 = datetime.now()
        nextMove["option"]["reset_callback_function_addr"] = self.update
        self.mode = GameStatus["judge_info"]["mode"]
        if self.init_train_parameter_flag == False:
            self.init_train_parameter_flag = True
            self.set_parameter(yaml_file=yaml_file,predict_weight=weight)        
        self.ind =GameStatus["block_info"]["currentShape"]["index"]
        curr_backboard = GameStatus["field_info"]["backboard"]
        self.board_data_width = GameStatus["field_info"]["width"]
        self.board_data_height = GameStatus["field_info"]["height"]

        curr_shape_class = GameStatus["block_info"]["currentShape"]["class"]
        next_shape_class= GameStatus["block_info"]["nextShape"]["class"]
        self.ShapeNone_index = GameStatus["debug_info"]["shape_info"]["shapeNone"]["index"]
        curr_piece_id =GameStatus["block_info"]["currentShape"]["index"]
        next_piece_id =GameStatus["block_info"]["nextShape"]["index"]
        reshape_backboard = self.get_reshape_backboard(curr_backboard)

        next_steps =self.get_next_func(curr_backboard, curr_piece_id, curr_shape_class)
        
        if self.mode=="train" or self.mode=="train_sample_qlearing" or self.mode=="train_sample2":
            epsilon = self.final_epsilon + (max(self.num_decay_epochs - self.epoch, 0) * (
                    self.initial_epsilon - self.final_epsilon) / self.num_decay_epochs)
            u = random()
            random_action = u <= epsilon
            next_actions, next_states = zip(*next_steps.items())
            next_states = torch.stack(next_states)
                       
            if torch.cuda.is_available():
                next_states = next_states.cuda()
        
            self.model.train()
            with torch.no_grad():
                predictions = self.model(next_states)[:, 0]

            if random_action:
                index = randint(0, len(next_steps) - 1)
            else:
                index = torch.argmax(predictions).item()
            next_state = next_states[index, :]
            action = next_actions[index]
            reward = self.reward_func(curr_backboard, action, curr_shape_class)
            
            done = False

            if self.double_dqn:
                next_backboard  = self.getBoard(curr_backboard, curr_shape_class, action[1], action[0])
                next２_steps =self.get_next_func(next_backboard,next_piece_id,next_shape_class)
                next2_actions, next2_states = zip(*next２_steps.items())
                next2_states = torch.stack(next2_states)
                if torch.cuda.is_available():
                    next2_states = next2_states.cuda()
                self.model.train()
                with torch.no_grad():
                    next_predictions = self.model(next2_states)[:, 0]
                next_index = torch.argmax(next_predictions).item()
                next2_state = next2_states[next_index, :]
            elif self.target_net:
                next_backboard  = self.getBoard(curr_backboard, curr_shape_class, action[1], action[0])
                next２_steps =self.get_next_func(next_backboard,next_piece_id,next_shape_class)
                next2_actions, next2_states = zip(*next２_steps.items())
                next2_states = torch.stack(next2_states)
                if torch.cuda.is_available():
                    next2_states = next2_states.cuda()
                self.target_model.train()
                with torch.no_grad():
                    next_predictions = self.target_model(next2_states)[:, 0]
                next_index = torch.argmax(next_predictions).item()
                next2_state = next2_states[next_index, :]
            else:
                next_backboard  = self.getBoard(curr_backboard, curr_shape_class, action[1], action[0])
                next２_steps =self.get_next_func(next_backboard,next_piece_id,next_shape_class)
                next2_actions, next2_states = zip(*next２_steps.items())
                next2_states = torch.stack(next2_states)
                if torch.cuda.is_available():
                    next2_states = next2_states.cuda()
                self.model.train()
                with torch.no_grad():
                    next_predictions = self.model(next2_states)[:, 0]
                                
                epsilon = self.final_epsilon + (max(self.num_decay_epochs - self.epoch, 0) * (
                self.initial_epsilon - self.final_epsilon) / self.num_decay_epochs)
                u = random()
                random_action = u <= epsilon
                if random_action:
                    next_index = randint(0, len(next2_steps) - 1)
                else:
                    next_index = torch.argmax(next_predictions).item()
                next2_state = next2_states[next_index, :]
                
            self.episode_memory.append([next_state, reward, next2_state,done])
            if self.prioritized_replay:
                self.PER.store()

            nextMove["strategy"]["direction"] = action[1]
            nextMove["strategy"]["x"] = action[0]
            nextMove["strategy"]["y_operation"] = 1
            nextMove["strategy"]["y_moveblocknum"] = 1
            if self.tetrominoes > self.max_tetrominoes:
                nextMove["option"]["force_reset_field"] = True
            self.state = next_state
        elif self.mode == "predict" or self.mode == "predict_sample":
            self.model.eval()
            next_actions, next_states = zip(*next_steps.items())
            next_states = torch.stack(next_states)
            predictions = self.model(next_states)[:, 0]
            index = torch.argmax(predictions).item()
            action = next_actions[index]
            nextMove["strategy"]["direction"] = action[1]
            nextMove["strategy"]["x"] = action[0]
            nextMove["strategy"]["y_operation"] = 1
            nextMove["strategy"]["y_moveblocknum"] = 1
        return nextMove
    
    def getSearchXRange(self, Shape_class, direction):
        minX, maxX, _, _ = Shape_class.getBoundingOffsets(direction)
        xMin = -1 * minX
        xMax = self.board_data_width - maxX
        return xMin, xMax

    def getShapeCoordArray(self, Shape_class, direction, x, y):
        coordArray = Shape_class.getCoords(direction, x, y)
        return coordArray

    def getBoard(self, board_backboard, Shape_class, direction, x):
        board = copy.deepcopy(board_backboard)
        _board = self.dropDown(board, Shape_class, direction, x)
        return _board

    def dropDown(self, board, Shape_class, direction, x):
        dy = self.board_data_height - 1
        coordArray = self.getShapeCoordArray(Shape_class, direction, x, 0)
        for _x, _y in coordArray:
            _yy = 0
            while _yy + _y < self.board_data_height and (_yy + _y < 0 or board[(_y + _yy) * self.board_data_width + _x] == self.ShapeNone_index):
                _yy += 1
            _yy -= 1
            if _yy < dy:
                dy = _yy
        _board = self.dropDownWithDy(board, Shape_class, direction, x, dy)
        return _board

    def dropDownWithDy(self, board, Shape_class, direction, x, dy):
        _board = board
        coordArray = self.getShapeCoordArray(Shape_class, direction, x, 0)
        for _x, _y in coordArray:
            _board[(_y + dy) * self.board_data_width + _x] = Shape_class.shape
        return _board


BLOCK_CONTROLLER_TRAIN = Block_Controller()
