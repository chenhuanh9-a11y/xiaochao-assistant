import sys

sys.stderr.write("intentional test startup failure\n")
raise SystemExit(17)
