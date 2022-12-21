import keras.backend as K
import numpy as np
from keras.callbacks import Callback, ModelCheckpoint


class HistoryCache:

    def __init__(self, his_len=10):
        self.history = [0] * his_len
        self.history_len = his_len
        self.cursor = 0
        self.len = 0

    def put(self, value):
        self.history[self.cursor] = value
        self.cursor += 1
        if self.cursor >= self.history_len:
            self.cursor = 0
        if self.len + 1 <= self.history_len:
            self.len += 1

    def mean(self):
        return np.array(self.history[0: self.len]).mean()


class SGDRScheduler(Callback):
    '''Cosine annealing learning rate scheduler with periodic restarts.
    # Usage
        ```python
            schedule = SGDRScheduler(min_lr=1e-5,
                                     max_lr=1e-2,
                                     steps_per_epoch=np.ceil(epoch_size/batch_size),
                                     lr_decay=0.9,
                                     cycle_length=5,
                                     mult_factor=1.5)
            model.fit(X_train, Y_train, epochs=100, callbacks=[schedule])
        ```
    # Arguments
        min_lr: The lower bound of the learning rate range for the experiment.
        max_lr: The upper bound of the learning rate range for the experiment.
        steps_per_epoch: Number of mini-batches in the dataset. Calculated as `np.ceil(epoch_size/batch_size)`.
        lr_decay: Reduce the max_lr after the completion of each cycle.
                  Ex. To reduce the max_lr by 20% after each cycle, set this value to 0.8.
        cycle_length: Initial number of epochs in a cycle.
        mult_factor: Scale epochs_to_restart after each full cycle completion.
        initial_epoch: Used to resume training, **note**: Other args must be same as last training.
    # References
        Blog post: jeremyjordan.me/nn-learning-rate
        Original paper: http://arxiv.org/abs/1608.03983
    '''

    def __init__(self,
                 min_lr,
                 max_lr,
                 steps_per_epoch,
                 lr_decay=1,
                 cycle_length=10,
                 mult_factor=2,
                 initial_epoch=0):

        self.min_lr = min_lr
        self.max_lr = max_lr
        self.lr_decay = lr_decay

        self.batch_since_restart = 0
        self.next_restart = cycle_length

        self.steps_per_epoch = steps_per_epoch

        self.cycle_length = cycle_length
        self.mult_factor = mult_factor

        self.history = {}

        self.recovery_status(initial_epoch)

    def recovery_status(self, initial_epoch):
        # Return to the last state when it was stopped.
        if initial_epoch < self.cycle_length:
            num_cycles = 0
        else:
            ratio = initial_epoch / self.cycle_length

            num_cycles = 0
            while ratio > 0:
                ratio -= self.mult_factor ** num_cycles
                num_cycles += 1

            # If haven't done
            if ratio < 0:
                num_cycles -= 1

        done_epochs = 0
        for _ in range(num_cycles):
            self.max_lr *= self.lr_decay
            done_epochs += self.cycle_length
            self.cycle_length = np.ceil(self.cycle_length * self.mult_factor)

        self.batch_since_restart = (initial_epoch - done_epochs) * self.steps_per_epoch

    def clr(self):
        '''Calculate the learning rate.'''
        fraction_to_restart = self.batch_since_restart / (self.steps_per_epoch * self.cycle_length)
        lr = self.min_lr + 0.5 * (self.max_lr - self.min_lr) * (1 + np.cos(fraction_to_restart * np.pi))
        return lr

    def on_train_begin(self, logs=None):
        '''Initialize the learning rate to the minimum value at the start of training.'''
        logs = logs or {}
        K.set_value(self.model.optimizer.lr, self.max_lr)

    def on_batch_end(self, batch, logs=None):
        '''Record previous batch statistics and update the learning rate.'''
        logs = logs or {}
        self.history.setdefault('lr', []).append(K.get_value(self.model.optimizer.lr))
        for k, v in logs.items():
            self.history.setdefault(k, []).append(v)

        self.batch_since_restart += 1
        K.set_value(self.model.optimizer.lr, self.clr())

    def on_epoch_end(self, epoch, logs=None):
        '''Check for end of current cycle, apply restarts when necessary.'''
        if epoch + 1 == self.next_restart:
            self.batch_since_restart = 0
            self.cycle_length = np.ceil(self.cycle_length * self.mult_factor)
            self.next_restart += self.cycle_length
            self.max_lr *= self.lr_decay
            self.best_weights = self.model.get_weights()

    def on_train_end(self, logs=None):
        '''Set weights to the values from the end of the most recent cycle for best performance.'''
        self.model.set_weights(self.best_weights)


class LRScheduler(Callback):

    def __init__(self, schedule, watch, watch_his_len=10):
        super().__init__()
        self.schedule = schedule
        self.watch = watch
        self.history_cache = HistoryCache(watch_his_len)

    def on_epoch_begin(self, epoch, logs=None):
        logs = logs or {}
        logs['lr'] = K.get_value(self.model.optimizer.lr)

    def on_epoch_end(self, epoch, logs=None):
        lr = float(K.get_value(self.model.optimizer.lr))
        watch_value = logs.get(self.watch)
        if watch_value is None:
            raise ValueError("Watched value '" + self.watch + "' don't exist")

        self.history_cache.put(watch_value)

        if watch_value > self.history_cache.mean():
            lr = self.schedule(epoch, lr)
            print("Update learning rate: ", lr)
            K.set_value(self.model.optimizer.lr, lr)


class SingleModelCK(ModelCheckpoint):
    """
    用于解决在多gpu下训练保存的权重无法应用于单gpu的情况
    """

    def __init__(self, filepath, model, monitor='val_loss', verbose=0,
                 save_best_only=False, save_weights_only=False,
                 mode='auto', period=1):
        super().__init__(filepath=filepath, monitor=monitor, verbose=verbose,
                         save_weights_only=save_weights_only,
                         save_best_only=save_best_only,
                         mode=mode, period=period)
        self.model = model

    def set_model(self, model):
        pass
