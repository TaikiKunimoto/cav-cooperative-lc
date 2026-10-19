#!/usr/bin/env bash
# straight.net.xml を作る。道路網の長さ・速度・車線幅は config/v2/build_networks.py に集めてあり、nod/edg/con もそこから生成する
# （手で直さない）。区間長を変えたら TraCI/v2/environment.py の区間長と締切も同じ値にする。
set -euo pipefail
cd "$(dirname "$0")"
python3 ../build_networks.py straight "$@"
