import os
import datetime
import logging
import colorlog

BERT_MODEL_NAME = {
    'bert-base': 'google/bert_uncased_L-12_H-768_A-12',
    'bert-mini': 'google/bert_uncased_L-4_H-256_A-4',
    'bert-tiny': 'google/bert_uncased_L-4_H-128_A-2'
}

BERT_OUTPUT_DIM = {
    'bert-base': 768,
    'bert-mini': 256,
    'bert-tiny': 128
}


def timeStamped(fname, fmt='{fname}_%Y%m%d_%H%M%S'):
    """
        Creates a timestamped filename, so we don't override our good work

        Input:
            fname: the given file name
            fmt: the format of timestamp
        Output:
            a new file name with timestamp added
    """
    return datetime.datetime.now().strftime(fmt).format(fname=fname)


def create_logger(exp_dir):
    logger = logging.getLogger("feature_learning")
    logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler(os.path.join(exp_dir, 'log.txt'))
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    stream_formatter = logging.Formatter('%(message)s')
    console_handler.setFormatter(stream_formatter)
    logger.addHandler(console_handler)

    return logger
