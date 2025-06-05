# Capsule-net for Urdu Digits Recognition

This repository contains code related to the publication **"Capsule-net for Urdu digits recognition"**.

## Dataset Preparation

Before running the code, make sure to:

- Split the dataset into two folders:
  - `data_train`
  - `data_test`

These folders should be placed in the root directory of the project and retain the same naming.

## Running the Code

To run the model from the terminal, use one of the following commands:

```bash
python capsulenet.py
or
```bash
python capsulenet.py --epochs 50
or
```bash
python capsulenet.py --epochs 50 --num_routing 3
