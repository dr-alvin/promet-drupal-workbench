import sys

from .guided_setup import Setup
from .workflow import Workflow

if __name__ == "__main__":
    Setup(Workflow(sys.argv[1])).work(sys.argv[2])
