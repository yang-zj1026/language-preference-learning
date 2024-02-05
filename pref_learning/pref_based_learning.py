# preference based learning

# Importing the libraries
import json
import os
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
import numpy as np
import argparse

from transformers import AutoModel, AutoTokenizer

from feature_learning.model import NLTrajAutoencoder
from feature_learning.nl_traj_dataset import NLTrajComparisonDataset
from feature_learning.learn_features import load_data
from feature_learning.utils import timeStamped, BERT_MODEL_NAME, BERT_OUTPUT_DIM, create_logger, AverageMeter
from model_analysis.utils import get_traj_lang_embeds
from model_analysis.improve_trajectory import initialize_reward, get_feature_value, get_lang_feedback

DEBUG = True

# learned and true reward func (linear for now)
class RewardFunc(nn.Module):
    def __init__(self, input_dim, output_dim):
        super(RewardFunc, self).__init__()
        self.linear = nn.Linear(input_dim, output_dim)

    def forward(self, x):
        return self.linear(x)


class Loss(nn.Module):
    def __init__(self):
        super(Loss, self).__init__()

    def forward(self, traj_cur, traj_opt, lang_feedback):
        # neg dot product
        dot_product = torch.exp(torch.dot(lang_feedback, (traj_opt - traj_cur)))
        return -dot_product


def run(args):

    # data
    # Load the test trajectories and language comparisons first
    trajs = np.load(f'{args.data_dir}/test/trajs.npy')
    nlcomps = json.load(open(f'{args.data_dir}/test/unique_nlcomps.json', 'rb'))
    nlcomps_bert_embeds = np.load(f'{args.data_dir}/test/unique_nlcomps_{args.bert_model}.npy')

    if DEBUG:
        print("len of trajs: " + str(len(trajs)))

    if (args.use_all_datasets):
        if DEBUG: print("using all datasets, so appending them....")
        # train+val+test all datasets
        # append train and val datasets
        trajs = np.append(trajs, np.load(f'{args.data_dir}/train/trajs.npy'), axis=0)
        nlcomps = json.load(open(f'{args.data_dir}/train/unique_nlcomps.json', 'rb')) + nlcomps
        nlcomps_bert_embeds = np.append(nlcomps_bert_embeds, np.load(f'{args.data_dir}/train/unique_nlcomps_{args.bert_model}.npy'), axis=0)

        trajs = np.append(trajs, np.load(f'{args.data_dir}/val/trajs.npy'), axis=0)
        nlcomps = json.load(open(f'{args.data_dir}/val/unique_nlcomps.json', 'rb')) + nlcomps
        nlcomps_bert_embeds = np.append(nlcomps_bert_embeds, np.load(f'{args.data_dir}/val/unique_nlcomps_{args.bert_model}.npy'), axis=0)

        if DEBUG: print("length of trajs after using all datasets: " + str(len(trajs)))

    # need to run categorize.py first 
    # classified_nlcomps = json.load(open(f'data/classified_nlcomps.json', 'rb'))
    greater_nlcomps = json.load(open(f'data/greater_nlcomps.json', 'rb'))
    less_nlcomps = json.load(open(f'data/less_nlcomps.json', 'rb'))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if DEBUG: print("greater nlcomps size: " + str(len(greater_nlcomps)))
    if DEBUG: print("less nlcomps size: " + str(len(less_nlcomps)))
    if DEBUG: print(greater_nlcomps)
    if DEBUG: print("device: " + str(device))

    # Find the optimal trajectory given the reward function
    feature_values = np.array([get_feature_value(traj) for traj in trajs])
    # Normalize feature values
    feature_values = (feature_values - np.min(feature_values, axis=0)) / (
            np.max(feature_values, axis=0) - np.min(feature_values, axis=0))

    less_idx = np.random.choice(5, size=2, replace=False)
    for i in less_idx:
        feature_values[:, i] = 1 - feature_values[:, i]

    # Current learned language encoder
    # Load the model
    if args.use_bert_encoder:
        lang_encoder = AutoModel.from_pretrained(BERT_MODEL_NAME[args.bert_model])
        tokenizer = AutoTokenizer.from_pretrained(BERT_MODEL_NAME[args.bert_model])
        feature_dim = BERT_OUTPUT_DIM[args.bert_model]
    else:
        lang_encoder = None
        tokenizer = None
        feature_dim = 128

    model = NLTrajAutoencoder(
        encoder_hidden_dim=args.encoder_hidden_dim,
        feature_dim=feature_dim,
        decoder_hidden_dim=args.decoder_hidden_dim,
        lang_encoder=lang_encoder,
        preprocessed_nlcomps=args.preprocessed_nlcomps,
        bert_output_dim=BERT_OUTPUT_DIM[args.bert_model],
        use_bert_encoder=args.use_bert_encoder,
        traj_encoder=args.traj_encoder,
        use_cnn_in_transformer=args.use_cnn_in_transformer,
        use_casual_attention=args.use_casual_attention
    )
    state_dict = torch.load(os.path.join(args.model_dir, 'best_model_state_dict.pth'))
    model.load_state_dict(state_dict)
    model.to(device)
    model.eval()

    # random init both reward functions (learned, true)
    learned_reward = RewardFunc(128, 1)
    true_reward = initialize_reward(5)
    nn.init.normal_(learned_reward.linear.weight, mean=0.5, std=0.01)

    # loss func
    criteria = Loss()
    optimizer = torch.optim.Adam(learned_reward.parameters(), lr=args.lr, weight_decay=args.weight_decay)


    traj_embeds, lang_embeds = get_traj_lang_embeds(trajs, nlcomps, model, device, args.use_bert_encoder, tokenizer, nlcomps_bert_embeds)

    # split into batches
    feature_values = np.split(feature_values, args.num_batches)
    traj_embeds = np.split(traj_embeds, args.num_batches)
    lang_embeds = np.split(lang_embeds, args.num_batches)

    # split trajs into arg.num_batches batches
        # test size = 22 (172 train, 22 val)
        # possible batch sizes: 1, 2, 11, 22
    if (args.num_batches > len(trajs)):
        args.num_batches = len(trajs)
    trajs = np.split(trajs, args.num_batches)

    np.random.seed(args.seed)

    for batch_num, batch in enumerate(trajs):
        # random select traj in current batch
        rand = np.random.randint(0, len(batch))

        # TODO: is this the optimal traj..? do the dimensions even align
        true_traj_opt_i = np.argmax(np.dot(feature_values[batch_num], true_reward.T))

        for i in range(args.num_iterations):
            # use current learned encoder for traj to get features
            # TODO: dim 5?
            # TODO: diff between this and feature_values[rand]?
            traj_cur_embed = traj_embeds[batch_num][rand]

            # Use true reward func to get language feedback (select from set)
            nlcomp = get_lang_feedback(feature_values[batch_num][true_traj_opt_i], feature_values[batch_num][rand], true_reward, less_idx, greater_nlcomps, less_nlcomps, args.use_softmax)

            # Based on language feedback, use learned lang encoder to get the feature in that feedback
            # nlcomp_feature = model.lang_encoder(nlcomp)
            nlcomp_feature = lang_embeds[batch_num][nlcomps.index(nlcomp)]
            

            # Select optimal traj based on current reward func
            cur_traj_opt_i = np.argmax(np.dot(feature_values[batch_num], learned_reward.numpy().T))
            traj_opt_embed = traj_embeds[batch_num][cur_traj_opt_i]

            # Compute dot product of lang(traj_opt - traj_cur)
            # Minimize the negative dot product (loss)!
            loss = criteria(traj_cur_embed, traj_opt_embed, nlcomp_feature)
            # Backprop
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()



        # TODO: calc cross-entropy for learned reward function analysis
        # two trajs
        # learned reward function with softmax
        # true reward function with softmax
        # cross-entropy with the Bernoulli distr from each


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='')

    parser.add_argument('--data-dir', type=str, default='data', help='')
    parser.add_argument('--model-dir', type=str, default='models', help='')
    parser.add_argument('--num-batches', type=int, default=2, help='')
    parser.add_argument('--encoder-hidden-dim', type=int, default=128)
    parser.add_argument('--decoder-hidden-dim', type=int, default=128)
    parser.add_argument('--preprocessed-nlcomps', action='store_true', help="")
    parser.add_argument('--bert-model', type=str, default='bert-base', help='which BERT model to use')
    parser.add_argument('--use-bert-encoder', action="store_true", help='whether to use BERT in the language encoder')
    parser.add_argument('--traj-encoder', default='mlp', choices=['mlp', 'transformer', 'lstm'],
                        help='which trajectory encoder to use')
    parser.add_argument('--use-cnn-in-transformer', action="store_true", help='whether to use CNN in the transformer')
    parser.add_argument('--use-casual-attention', action="store_true",
                        help='whether to use casual attention in the transformer')
    parser.add_argument('--weight-decay', type=float, default=0, help='')
    parser.add_argument('--lr', type=float, default=1e-3, help='')
    parser.add_argument('--seed', type=int, default=0, help='')
    parser.add_argument('--num-iterations', type=int, default=10, help='')
    parser.add_argument('--use-all-datasets', action="store_true", help='whether to use all datasets or just test set')
    parser.add_argument('--use-softmax', action="store_true", help='whether to use softmax or argmax for feedback')

    args = parser.parse_args()
    run(args)
