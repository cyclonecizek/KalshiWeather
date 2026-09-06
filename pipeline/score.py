"""Compatibility entry point for horizon-specific performance scoring."""
from .performance import publish

def main():
    r=publish()
    print(f"Scored {len(r['records'])} matched forecasts in {len(r['groups'])} groups")
    return 0

if __name__=="__main__":
    main()
