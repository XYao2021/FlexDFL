import torch
import random
import copy
import numpy as np
from torch.utils.data import DataLoader
from model.model import Model
from util.util import *
from compression import *
from partition import *
from config import *
from dataset.dataset import *
from trans_matrix import *
import time
from datetime import date
import os


if device != 'cpu':
    current_device = torch.cuda.current_device()
    torch.cuda.set_device(current_device)

if __name__ == "__main__":  #TODO: Why use this sentence
    ACC = []
    LOSS = []
    COMM = []

    for seed in Seed_set:
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed(seed)

        train_data, test_data = loading(dataset_name=dataset, data_path=dataset_path, device=device)
        train_loader = DataLoader(train_data, batch_size=BATCH_SIZE_TEST, shuffle=True, num_workers=0)
        test_loader = DataLoader(test_data, batch_size=BATCH_SIZE_TEST, shuffle=False, num_workers=0)

        #TODO: Change the sampling and splitting method to class to accelerate the set-up process
        Sample = Sampling(num_client=CLIENTS, num_class=len(train_data.classes), train_data=train_data, method='uniform', seed=seed)
        if DISTRIBUTION == 'Dirichlet':
            client_data = Sample.Synthesize_sampling(alpha=ALPHA)
        elif DISTRIBUTION == 'Single':
            client_data = Sample.DL_sampling_single()
        else:
            raise Exception('This data distribution method has not been embedded')

        client_train_loader = []
        client_residual = []
        client_compressor = []
        Models = []
        client_weights = []

        for n in range(CLIENTS):
            model = Model(random_seed=seed, learning_rate=LEARNING_RATE, model_name=model_name, device=device, flatten_weight=True, pretrained_model_file=load_model_file)
            Models.append(model)
            client_weights.append(model.get_weights())
            client_train_loader.append(DataLoader(client_data[n], batch_size=BATCH_SIZE, shuffle=True))
            client_residual.append(torch.zeros_like(model.get_weights()).to(device))

            # client_compressor.append(Top_k(node=n, avg_comm_cost=average_comm_cost, ratio=RATIO))
            # client_compressor.append(Rand_k(node=n, avg_comm_cost=average_comm_cost, ratio=RATIO))
            client_compressor.append(Quantization(num_bits=QUANTIZE_LEVEL))

        Transfer = Transform(num_nodes=CLIENTS, num_neighbors=NEIGHBORS, seed=seed, network='random')
        check = Check_Matrix(CLIENTS, Transfer.matrix)
        if check != 0:
            raise Exception('The Transfer Matrix Should be Symmetric')
        else:
            print('Transfer Matrix is Symmetric Matrix')

        print(Transfer.neighbors)
        print(Transfer.factor)
        test_model = Model(random_seed=seed, learning_rate=LEARNING_RATE, model_name=model_name, device=device, flatten_weight=True, pretrained_model_file=load_model_file)

        global_loss = []
        Test_acc = []
        iter_num = 0
        total_comm_num = 0

        while True:  # TODO: What is the difference with for loop over clients
            print('SEED ', '|', seed, '|', 'ITERATION ', iter_num)
            Total_Update = []
            Update = []

            for n in range(CLIENTS):
                Models[n].assign_weights(weights=client_weights[n])
                Models[n].model.train()

                for local_iter in range(ROUND_ITER):
                    images, labels = next(iter(client_train_loader[n]))
                    images, labels = images.to(device), labels.to(device)
                    if data_transform is not None:
                        images = data_transform(images)

                    Models[n].optimizer.zero_grad()
                    pred = Models[n].model(images)
                    loss = Models[n].loss_function(pred, labels)
                    loss.backward()
                    Models[n].optimizer.step()

                    Vector_update = Models[n].get_weights()
                    Vector_update -= client_weights[n]

                Vector_update, client_residual[n] = client_compressor[n].get_trans_bits_and_residual(iter=iter_num, w_tmp=Vector_update, w_residual=client_residual[n])

                Vector_update += client_weights[n]
                Total_Update.append(Vector_update)

            Total_Update = Transfer.Average(Total_Update)
            for client in range(CLIENTS):
                client_weights[client] = Total_Update[client]

            iter_num += 1

            # train_loss, train_acc = test_model.accuracy(weights=client_weights[0], test_loader=train_loader, device=device)
            # test_loss, test_acc = test_model.accuracy(weights=client_weights[0], test_loader=test_loader, device=device)
            test_weights = [Models[j].get_weights() for j in range(CLIENTS)]
            test_weights = average_weights(test_weights)
            train_loss, train_acc = test_model.accuracy(weights=test_weights, test_loader=train_loader, device=device)
            test_loss, test_acc = test_model.accuracy(weights=test_weights, test_loader=test_loader, device=device)

            global_loss.append(train_loss)
            Test_acc.append(test_acc)
            print('SEED |', seed, '| iteration |', iter_num, '| Global Loss', train_loss, '| Training Accuracy |',
                  train_acc, '| Test Accuracy |', test_acc)

            if iter_num >= AGGREGATION:
                ACC += Test_acc
                LOSS += global_loss
                COMM.append(total_comm_num)
                break

        del Models
        del client_weights
        del Total_Update

        torch.cuda.empty_cache()  # Clean the memory cache

    # txt_list = [ACC, '\n', LOSS, '\n', COMP_COST, '\n', COMM_COST]
    txt_list = [ACC, '\n', LOSS]
    f = open('RCD|{}|{}|{}|{}|{}|{}.txt'.format(ROUND_ITER, CLIENTS, NEIGHBORS, ALPHA, date.today(), time.strftime("%H:%M:%S", time.localtime())), 'w')

    # f = open('PRO_{}_{}.txt'.format(RATIO, ROUND_ITER), 'w')
    for item in txt_list:
        f.write("%s\n" % item)
    # whole length of weights: 39760

    # for repeat_time in range(3):
    #     os.system('say "Program Finished."')
    #  tensor(8.9569, device='cuda:0') tensor(6.7312, device='cuda:0')

    # Residual Compensation Decentralized SGD (RCD-SGD)
