import sys

from .workflow import Workflow

if __name__ == "__main__":
    Workflow(sys.argv[1]).work(sys.argv[2])
