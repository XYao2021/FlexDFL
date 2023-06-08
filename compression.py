import copy
from abc import ABC

import torch
import numpy as np
import abc
from config import *
from numpy.random import RandomState, SeedSequence
from numpy.random import MT19937


def communication_cost(node, iter, full_size, trans_size):
    if trans_size == 0:
        return 0.0
    else:
        rs = RandomState(MT19937(SeedSequence(iter * 800 + node + 23456)))

        if node >= 0:
            constant = 0.05  # beta
            return constant + float(trans_size)/full_size / np.log2(1 + rs.chisquare(df=2))
        else:
            constant = 0.01  # smaller at server
            return constant + 0.2 * float(trans_size)/full_size / np.log2(1 + rs.chisquare(df=2))

class Compression(abc.ABC):
    def __init__(self, node):
        self.node = node

    def get_trans_bits_and_residual(self, iter, w_tmp, w_residual):  # w_tmp is gradient this time
        if w_tmp is None:
            G = 0
            w_tmp = w_residual  # w_residual is e_t
        else:
            G = torch.sum(torch.square(w_tmp))
            w_tmp += w_residual

        trans_indices, not_trans_indices, Bt = self._get_trans_indices(iter, w_tmp)

        w_tmp_residual = copy.deepcopy(w_tmp)
        w_tmp[not_trans_indices] = 0  # transfer vector v_t, sparse vector
        w_tmp_residual -= w_tmp  # accumulate the residual for not transmit bits

        E = torch.sum(torch.square(w_tmp_residual))
        return w_tmp, w_tmp_residual, Bt, E, G

    def _get_trans_indices(self, iter, w_tmp):
        raise NotImplementedError()  #TODO: What does this mean?

class Lyapunov_compression(Compression):
    def __init__(self, node, avg_comm_cost, V, W):
        super().__init__(node)
        self.avg_comm_cost = avg_comm_cost
        self.V = V
        self.queue = W  # Initial queue length

    def _get_trans_indices(self, iter, w_tmp):
        # full_size = w_tmp.size()[0]
        full_size = w_tmp.shape[0]
        bt_square = torch.square(w_tmp)
        Bt = torch.sum(bt_square)
        bt_sq_sort, bt_sq_sort_indices = torch.sort(bt_square, descending=True)

        no_transmit_penalty = self.V * torch.sum(bt_square) - self.queue * self.avg_comm_cost
        cost_delta = self.queue * (communication_cost(self.node, iter, full_size, 2) - communication_cost(self.node, iter, full_size, 1))  # equal to gamma_t * PHI_t(queue at time t)

        tmp = torch.arange(bt_square.shape[0], device=device)
        tmp2 = tmp[self.V * bt_sq_sort <= cost_delta]
        if len(tmp2) > 0:
            j = tmp2[0]
            # print(self.node, len(tmp2), j)
        else:
            j = full_size
        drift_plus_penalty = self.V * torch.sum(bt_sq_sort[j:]) + \
                             self.queue * (communication_cost(self.node, iter, full_size, j) - self.avg_comm_cost)
        if drift_plus_penalty < no_transmit_penalty:
            trans_bits = j
        else:
            trans_bits = 0
        self.queue += communication_cost(self.node, iter, full_size, trans_bits) - self.avg_comm_cost
        self.queue = max(0.001, self.queue)  # Not allow to have the negative queues, set to very small one
        return bt_sq_sort_indices[:trans_bits], bt_sq_sort_indices[trans_bits:], Bt

class Fixed_Compression(Compression):
    def __init__(self, node, avg_comm_cost, ratio=1.0):
        super().__init__(node)
        self.avg_comm_cost = avg_comm_cost
        self.ratio = ratio

    def _get_trans_indices(self, iter, w_tmp):
        full_size = w_tmp.size()[0]
        bt_square = torch.square(w_tmp)
        bt_square_sorted, bt_sorted_indices = torch.sort(bt_square, descending=True)

        no_trans_cost = communication_cost(self.node, iter, full_size, 0)
        if no_trans_cost > 0:
            raise Exception('No transmit cost should be zero')

        k = int(full_size * self.ratio)
        if k > torch.count_nonzero(bt_square).item():
            k = torch.count_nonzero(bt_square).item()
        trans_cost = communication_cost(self.node, iter, full_size, k)

        if trans_cost > 0:
            p_trans = min(1.0, self.avg_comm_cost / trans_cost)
        else:
            p_trans = 1.0

        if np.random.binomial(1, p_trans) == 1:
            trans_bits = k
        else:
            trans_bits = 0
        return bt_sorted_indices[:trans_bits], bt_sorted_indices[trans_bits:], communication_cost(self.node, iter, full_size, trans_bits)

class Normal_Compression(abc.ABC):
    def __init__(self, node):
        self.node = node

    def get_trans_bits_and_residual(self, w_tmp, w_residual):  # w_tmp is gradient this time
        if w_tmp is None:
            w_tmp = w_residual  # w_residual is e_t
        else:
            w_tmp += w_residual

        trans_indices, not_trans_indices = self._get_trans_indices(w_tmp)

        w_tmp_residual = copy.deepcopy(w_tmp)
        w_tmp[not_trans_indices] = 0  # transfer vector v_t, sparse vector
        w_tmp_residual -= w_tmp  # accumulate the residual for not transmit bits
        return w_tmp, w_tmp_residual

    def _get_trans_indices(self, w_tmp):
        raise NotImplementedError()  #TODO: What does this mean?

class Top_k(Normal_Compression):
    def __init__(self, node, ratio=1.0):
        super().__init__(node)
        self.ratio = ratio

    def _get_trans_indices(self, w_tmp):
        full_size = w_tmp.size()[0]
        bt_square = torch.square(w_tmp)
        bt_square_sorted, bt_sorted_indices = torch.sort(bt_square, descending=True)

        k = int(full_size * self.ratio)
        if k > torch.count_nonzero(bt_square).item():
            k = torch.count_nonzero(bt_square).item()
        return bt_sorted_indices[:k], bt_sorted_indices[k:]

class Rand_k(Normal_Compression):
    def __init__(self, node, ratio=1.0):
        super().__init__(node)
        self.ratio = ratio

    def _get_trans_indices(self, w_tmp):
        np.random.seed()
        full_size = w_tmp.size()[0]
        all_indices = np.arange(full_size, dtype=int)
        send_indices = np.random.choice(all_indices, int(full_size*self.ratio), replace=False)
        # print(self.node, send_indices)
        not_send_indices = np.setdiff1d(all_indices, send_indices)
        return send_indices, not_send_indices
