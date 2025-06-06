import torch
import torch.nn as nn

from tqdm import tqdm
from src.loss import L2RegularizedCrossEntropyLoss
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score
from sklearn.model_selection import StratifiedShuffleSplit
from sklearn.metrics import confusion_matrix
import random

import numpy as np
# import matplotlib.pyplot as plt
# import seaborn as sns
from src.utils import get_module_device

def evaluate(test_loader, model, criterion, device=None, log=False):
    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.eval()
    pbar = tqdm(test_loader, desc='eval', unit='batch')
    correct = 0
    total = 0
    with torch.no_grad():
        for inputs, targets in pbar:
            inputs, targets = inputs.to(device), targets.to(device)
            outputs = model(inputs)
            if isinstance(criterion, L2RegularizedCrossEntropyLoss):
                loss = criterion(outputs, targets, model)
            else:
                loss = criterion(outputs, targets)
            _, predicted = outputs.max(1)
            total += targets.size(0)
            correct += predicted.eq(targets).sum().item()
            pbar.set_postfix(loss=loss.item(), acc=correct / total)
        pbar.close()
    if log:
        return correct / total


def cm_score(estimator, X, y):
    y_pred = estimator.predict(X)
    cnf_matrix = confusion_matrix(y, y_pred)

    FP = cnf_matrix[0][1]
    FN = cnf_matrix[1][0]
    TP = cnf_matrix[0][0]
    TN = cnf_matrix[1][1]

    # Sensitivity, hit rate, recall, or true positive rate
    TPR = TP / (TP + FN)
    # Specificity or true negative rate
    TNR = TN / (TN + FP)
    # Precision or positive predictive value
    PPV = TP / (TP + FP)
    # Negative predictive value
    NPV = TN / (TN + FN)
    # Fall out or false positive rate
    FPR = FP / (FP + TN)
    # False negative rate
    FNR = FN / (TP + FN)
    # False discovery rate
    FDR = FP / (TP + FP)

    # Overall accuracy
    ACC = (TP + TN) / (TP + FP + FN + TN)
    print(f"FPR:{FPR:.2f}, FNR:{FNR:.2f}, FP{FP:.2f}, TN{TN:.2f}, TP{TP:.2f}, FN{FN:.2f}")
    return ACC


def evaluate_attack_model(sample_loss,
                          members,
                          n_splits=5,
                          random_state=None):
    """Computes the cross-validation score of a membership inference attack.
    Args:
      sample_loss : array_like of shape (n,).
        objective function evaluated on n samples.
      members : array_like of shape (n,),
        whether a sample was used for training.
      n_splits: int
        number of splits to use in the cross-validation.
      random_state: int, RandomState instance or None, default=None
        random state to use in cross-validation splitting.
    Returns:
      score : array_like of size (n_splits,)
    """

    unique_members = np.unique(members)
    if not np.all(unique_members == np.array([0, 1])):
        raise ValueError("members should only have 0 and 1s")

    attack_model = LogisticRegression()
    cv = StratifiedShuffleSplit(
        n_splits=n_splits, random_state=random_state)
    return cross_val_score(attack_model, sample_loss, members, cv=cv, scoring=cm_score)


def membership_inference_attack(model, t_loader, f_loader, seed=42):
    device = get_module_device(model)
    fgt_cls = list(np.unique(f_loader.dataset.targets))
    indices = [i in fgt_cls for i in t_loader.dataset.targets]
    t_loader.dataset.data = t_loader.dataset.data[indices]
    t_loader.dataset.targets = t_loader.dataset.targets[indices]

    cr = nn.CrossEntropyLoss(reduction='none')
    test_losses = []
    forget_losses = []
    model.eval()
    mult = 1
    dataloader = torch.utils.data.DataLoader(t_loader.dataset, batch_size=128, shuffle=False)
    for batch_idx, (data, target) in enumerate(dataloader):
        data, target = data.to(device), target.to(device)
        output = model(data)
        loss = mult * cr(output, target)
        test_losses = test_losses + list(loss.cpu().detach().numpy())
    del dataloader
    dataloader = torch.utils.data.DataLoader(f_loader.dataset, batch_size=128, shuffle=False)
    for batch_idx, (data, target) in enumerate(dataloader):
        data, target = data.to(device), target.to(device)
        output = model(data)
        loss = mult * cr(output, target)
        forget_losses = forget_losses + list(loss.cpu().detach().numpy())
    del dataloader

    np.random.seed(seed)
    random.seed(seed)
    if len(forget_losses) > len(test_losses):
        forget_losses = list(random.sample(forget_losses, len(test_losses)))
    elif len(test_losses) > len(forget_losses):
        test_losses = list(random.sample(test_losses, len(forget_losses)))

    # sns.distplot(np.array(test_losses), kde=False, norm_hist=False, rug=False, label='test-loss', ax=plt)
    # sns.distplot(np.array(forget_losses), kde=False, norm_hist=False, rug=False, label='forget-loss', ax=plt)
    # plt.legend(prop={'size': 14})
    # plt.tick_params(labelsize=12)
    # plt.title("loss histograms", size=18)
    # plt.xlabel('loss values', size=14)
    # plt.show()
    # print(np.max(test_losses), np.min(test_losses))
    # print(np.max(forget_losses), np.min(forget_losses))

    test_labels = [0] * len(test_losses)
    forget_labels = [1] * len(forget_losses)
    features = np.array(test_losses + forget_losses).reshape(-1, 1)
    labels = np.array(test_labels + forget_labels).reshape(-1)
    features = np.clip(features, -100, 100)
    score = evaluate_attack_model(features, labels, n_splits=5, random_state=seed)

    return score