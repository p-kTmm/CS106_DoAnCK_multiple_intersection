# CS106_DoAn
Supported by: https://github.com/stuti2403/Traffic-Light-Management-system-using-RL-and-SUMO.git


## Prepare the environment: 
All main packages are included ```requirements.txt```.
```bash
pip install -r requirements.txt
```

## Train command:
```bash
python train.py --train -e 50 -m model_name -s 2000
```


## Test comand:
```bash
python train.py -m model_name -s 2000
```

## Pararmeter explainations:
- **train**: enable training process if set to *True* otherwise testing mode is enabled (default is False).  
- **e**: the number of epoch for training process (default is 50).
- **m**: name the new model if train or else indicate the model to test.
- **s**: the numbers of steps in simulation.

