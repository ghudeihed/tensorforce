# Copyright 2020 Tensorforce Team. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

import argparse
import os
import pickle

import ConfigSpace as cs
from hpbandster.core.nameserver import NameServer, nic_name_to_host
from hpbandster.core.result import json_result_logger, logged_results_to_HBS_result
from hpbandster.core.worker import Worker
from hpbandster.optimizers import BOHB
import numpy as np

from tensorforce.environments import Environment
from tensorforce.execution import Runner


class TensorforceWorker(Worker):

    def __init__(self, *args, environment, max_episode_timesteps=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.environment = environment
        self.max_episode_timesteps = max_episode_timesteps

    def compute(self, config_id, config, budget, working_directory):
        frequency = max(1, int(config['frequency'] * config['batch_size']))
        update = dict(unit='episodes', batch_size=config['batch_size'], frequency=frequency)

        policy = dict(network=dict(type='auto', size=64, depth=2, rnn=False))
        optimizer = dict(type='adam', learning_rate=config['learning_rate'])
        if config['ratio_based'] == 'yes':
            objective = dict(type='policy_gradient', ratio_based=True, clipping_value=0.2)
        else:
            objective = dict(type='policy_gradient', ratio_based=False, clipping_value=0.2)

        horizon = config['horizon']
        discount = config['discount']

        if config['baseline'] == 'no':
            predict_horizon_values = False
            estimate_advantage = False
            predict_action_values = False
            baseline_policy = None
            baseline_optimizer = None
            baseline_objective = None
        elif config['baseline'] == 'same':
            predict_horizon_values = 'early'
            estimate_advantage = (config['estimate_advantage'] == 'yes')
            predict_action_values = False
            baseline_policy = None
            baseline_optimizer = config['baseline_weight']
            baseline_objective = dict(type='value', value='state')
        elif config['baseline'] == 'yes':
            predict_horizon_values = 'early'
            estimate_advantage = (config['estimate_advantage'] == 'yes')
            predict_action_values = False
            baseline_policy = dict(network=dict(type='auto', size=64, depth=2, rnn=False))
            baseline_optimizer = baseline_optimizer = dict(
                type='adam', learning_rate=config['baseline_learning_rate']
            )
            baseline_objective = dict(type='value', value='state')
        else:
            assert False

        if config['entropy_regularization'] < 3e-5:
            entropy_regularization = 0.0
        else:
            entropy_regularization = config['entropy_regularization']

        agent = dict(
            policy=policy, memory='recent', update=update, optimizer=optimizer, objective=objective,
            reward_estimation=dict(
                horizon=horizon, discount=discount, predict_horizon_values=predict_horizon_values,
                estimate_advantage=estimate_advantage, predict_action_values=predict_action_values
            ),
            baseline_policy=baseline_policy, baseline_optimizer=baseline_optimizer,
            baseline_objective=baseline_objective, entropy_regularization=entropy_regularization
        )

        # num_episodes = list()
        final_reward = list()
        max_reward = list()
        rewards = list()

        for n in range(round(budget)):
            runner = Runner(
                agent=agent, environment=self.environment,
                max_episode_timesteps=self.max_episode_timesteps
            )
            runner.run(num_episodes=200, use_tqdm=False)
            runner.close()

            # num_episodes.append(len(runner.episode_rewards))
            final_reward.append(float(np.mean(runner.episode_rewards[-20:], axis=0)))
            average_rewards = [
                float(np.mean(runner.episode_rewards[n: n + 20], axis=0))
                for n in range(len(runner.episode_rewards) - 20)
            ]
            max_reward.append(float(np.amax(average_rewards, axis=0)))
            rewards.append(list(runner.episode_rewards))

        # mean_num_episodes = float(np.mean(num_episodes, axis=0))
        mean_final_reward = float(np.mean(final_reward, axis=0))
        mean_max_reward = float(np.mean(max_reward, axis=0))
        # loss = mean_num_episodes - mean_final_reward - mean_max_reward
        loss = -mean_final_reward - mean_max_reward

        return dict(loss=loss, info=dict(rewards=rewards))

    @staticmethod
    def get_configspace():
        configspace = cs.ConfigurationSpace()

        batch_size = cs.hyperparameters.UniformIntegerHyperparameter(
            name='batch_size', lower=1, upper=50, log=True
        )
        configspace.add_hyperparameter(hyperparameter=batch_size)

        frequency = cs.hyperparameters.UniformFloatHyperparameter(
            name='frequency', lower=1e-2, upper=1.0, log=True
        )
        configspace.add_hyperparameter(hyperparameter=frequency)

        learning_rate = cs.hyperparameters.UniformFloatHyperparameter(
            name='learning_rate', lower=1e-5, upper=0.1, log=True
        )
        configspace.add_hyperparameter(hyperparameter=learning_rate)

        horizon = cs.hyperparameters.UniformIntegerHyperparameter(
            name='horizon', lower=1, upper=100, log=True
        )
        configspace.add_hyperparameter(hyperparameter=horizon)

        discount = cs.hyperparameters.UniformFloatHyperparameter(
            name='discount', lower=0.8, upper=1.0, log=True
        )
        configspace.add_hyperparameter(hyperparameter=discount)

        ratio_based = cs.hyperparameters.CategoricalHyperparameter(
            name='ratio_based', choices=('no', 'yes')
        )
        configspace.add_hyperparameter(hyperparameter=ratio_based)

        baseline = cs.hyperparameters.CategoricalHyperparameter(
            name='baseline', choices=('no', 'same', 'yes')
        )
        configspace.add_hyperparameter(hyperparameter=baseline)

        baseline_weight = cs.hyperparameters.UniformFloatHyperparameter(
            name='baseline_weight', lower=1e-2, upper=1e2
        )
        configspace.add_hyperparameter(hyperparameter=baseline_weight)

        baseline_learning_rate = cs.hyperparameters.UniformFloatHyperparameter(
            name='baseline_learning_rate', lower=1e-5, upper=0.1, log=True
        )
        configspace.add_hyperparameter(hyperparameter=baseline_learning_rate)

        estimate_advantage = cs.hyperparameters.CategoricalHyperparameter(
            name='estimate_advantage', choices=('no', 'yes')
        )
        configspace.add_hyperparameter(hyperparameter=estimate_advantage)

        entropy_regularization = cs.hyperparameters.UniformFloatHyperparameter(
            name='entropy_regularization', lower=1e-5, upper=1.0, log=True
        )
        configspace.add_hyperparameter(hyperparameter=entropy_regularization)

        configspace.add_condition(
            condition=cs.NotEqualsCondition(
                child=estimate_advantage, parent=baseline, value='no'
            )
        )
        configspace.add_condition(
            condition=cs.EqualsCondition(
                child=baseline_weight, parent=baseline, value='same'
            )
        )
        configspace.add_condition(
            condition=cs.EqualsCondition(
                child=baseline_learning_rate, parent=baseline, value='yes'
            )
        )

        return configspace


def main():
    parser = argparse.ArgumentParser(description='Tensorforce hyperparameter tuner')
    parser.add_argument(
        'environment', help='Environment (name, configuration JSON file, or library module)'
    )
    parser.add_argument(
        '-l', '--level', type=str, default=None,
        help='Level or game id, like `CartPole-v1`, if supported'
    )
    parser.add_argument(
        '-m', '--max-repeats', type=int, default=10, help='Maximum number of repetitions'
    )
    parser.add_argument(
        '-n', '--num-iterations', type=int, default=1, help='Number of BOHB iterations'
    )
    parser.add_argument(
        '-d', '--directory', type=str, default='tuner', help='Output directory'
    )
    parser.add_argument(
        '-r', '--restore', type=str, default=None, help='Restore from given directory'
    )
    parser.add_argument('--id', type=str, default='worker', help='Unique worker id')
    args = parser.parse_args()

    if args.level is None:
        environment = Environment.create(environment=args.environment)
    else:
        environment = Environment.create(environment=args.environment, level=args.level)

    if False:
        host = nic_name_to_host(nic_name=None)
        port = 123
    else:
        host = 'localhost'
        port = None

    server = NameServer(run_id=args.id, working_directory=args.directory, host=host, port=port)
    nameserver, nameserver_port = server.start()

    worker = TensorforceWorker(
        environment=environment, run_id=args.id, nameserver=nameserver,
        nameserver_port=nameserver_port, host=host
    )
    worker.run(background=True)

    if args.restore is None:
        previous_result = None
    else:
        previous_result = logged_results_to_HBS_result(directory=args.restore)

    result_logger = json_result_logger(directory=args.directory, overwrite=True)  # ???

    optimizer = BOHB(
        configspace=worker.get_configspace(), min_budget=0.5, max_budget=float(args.max_repeats),
        run_id=args.id, working_directory=args.directory,
        nameserver=nameserver, nameserver_port=nameserver_port, host=host,
        result_logger=result_logger, previous_result=previous_result
    )
    # BOHB(configspace=None, eta=3, min_budget=0.01, max_budget=1, min_points_in_model=None, top_n_percent=15, num_samples=64, random_fraction=1 / 3, bandwidth_factor=3, min_bandwidth=1e-3, **kwargs)
    # Master(run_id, config_generator, working_directory='.', ping_interval=60, nameserver='127.0.0.1', nameserver_port=None, host=None, shutdown_workers=True, job_queue_sizes=(-1,0), dynamic_queue_size=True, logger=None, result_logger=None, previous_result = None)
    # logger: logging.logger like object, the logger to output some (more or less meaningful) information

    results = optimizer.run(n_iterations=args.num_iterations)
    # optimizer.run(n_iterations=1, min_n_workers=1, iteration_kwargs={})
    # min_n_workers: int, minimum number of workers before starting the run

    optimizer.shutdown(shutdown_workers=True)
    server.shutdown()
    environment.close()

    with open(os.path.join(args.directory, 'results.pkl'), 'wb') as filehandle:
        pickle.dump(results, filehandle)

    print('Best found configuration: {}'.format(
        results.get_id2config_mapping()[results.get_incumbent_id()]['config']
    ))
    print('Runs:', results.get_runs_by_id(config_id=results.get_incumbent_id()))
    print('A total of {} unique configurations where sampled.'.format(
        len(results.get_id2config_mapping())
    ))
    print('A total of {} runs where executed.'.format(len(results.get_all_runs())))
    print('Total budget corresponds to {:.1f} full function evaluations.'.format(
        sum([r.budget for r in results.get_all_runs()]) / args.max_repeats)
    )


if __name__ == '__main__':
    main()


# python tune.py benchmarks/configs/cartpole.json 
