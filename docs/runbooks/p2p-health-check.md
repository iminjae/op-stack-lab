# Runbook — OP Stack P2P Health Check

## 목적

Mintaray OP Stack의 Pi5 Sequencer와 Pi4 Verifier 사이의 P2P 연결 상태를 확인하고,
신규 unsafe block이 정상적으로 Verifier까지 전달되는지 점검하기 위한 운영 절차다.

현재 구조:

~~~text
Pi5 Sequencer
    │
    │ OP Stack P2P
    │ TCP/UDP 9222
    ▼
Pi4 Verifier

safe / finalized 상태는 별도로 L1 derivation을 통해 검증
~~~

---

## 정상 상태 기준

다음 조건을 만족하면 P2P는 정상으로 판단한다.

- Pi5 P2P host가 실행 중
- Pi5 TCP/UDP 9222 listener 활성
- Pi4에서 Pi5 static peer 연결
- `totalConnected >= 1`
- `connected >= 1`
- `gossipBlocks = true`
- blocks gossip topic 참여
- Pi4 `unsafe_l2`가 Pi5 `unsafe_l2`와 거의 동일하게 진행
- 신규 트랜잭션이 Pi4 RPC에 수초 내 반영

---

# 1. Pi5 Sequencer P2P 상태 확인

Pi5에서 실행한다.

~~~bash
cd /opt/opstack/compose

curl -s \
  -H 'Content-Type: application/json' \
  --data '{
    "jsonrpc":"2.0",
    "method":"opp2p_self",
    "params":[],
    "id":1
  }' \
  http://127.0.0.1:8547 \
| jq '{
    peerID: .result.peerID,
    addresses: .result.addresses
  }'
~~~

정상 예:

~~~text
peerID = 16Uiu2H...
addresses:
- /ip4/127.0.0.1/tcp/9222/...
- /ip4/<docker-ip>/tcp/9222/...
~~~

Peer ID는 P2P identity이며 private key가 아니다.

---

# 2. Pi5 P2P Listener 확인

Pi5:

~~~bash
sudo ss -lntup | grep ':9222'
~~~

정상 상태에서는 TCP와 UDP가 모두 보여야 한다.

~~~text
tcp LISTEN ... 192.168.45.11:9222
udp UNCONN ... 192.168.45.11:9222
~~~

아무것도 출력되지 않으면 Pi5 op-node P2P listener가 활성화되지 않은 상태다.

---

# 3. Pi5 op-node 상태 확인

~~~bash
cd /opt/opstack/compose

docker compose ps op-node
~~~

정상:

~~~text
STATUS = Up
~~~

P2P 관련 최근 로그:

~~~bash
docker compose logs \
  --since=5m \
  op-node \
| grep -Ei 'p2p|peer|gossip|error|failed' \
| tail -100
~~~

정상 시작 시 다음과 유사한 로그가 존재한다.

~~~text
started p2p host
registered API namespace=opp2p
P2P RPC enabled
~~~

---

# 4. Pi4 → Pi5 네트워크 연결 확인

Pi4에서 실행한다.

~~~bash
timeout 3 bash -c '</dev/tcp/192.168.45.11/9222' \
  && echo "PI5_P2P_TCP=OK" \
  || echo "PI5_P2P_TCP=FAILED"
~~~

정상:

~~~text
PI5_P2P_TCP=OK
~~~

실패하면 다음 항목을 확인한다.

- Pi5 op-node 실행 상태
- Pi5 9222 listener
- Pi4 ↔ Pi5 LAN 연결
- Docker port mapping
- host firewall

---

# 5. Pi4 Connected Peer 확인

Pi4:

~~~bash
curl -s \
  -H 'Content-Type: application/json' \
  --data '{
    "jsonrpc":"2.0",
    "method":"opp2p_peers",
    "params":[true],
    "id":1
  }' \
  http://127.0.0.1:8547 \
| jq '{
    totalConnected: .result.totalConnected,
    peers: [
      .result.peers[]? |
      {
        peerID,
        connectedness,
        gossipBlocks,
        addresses
      }
    ]
  }'
~~~

정상 기준:

~~~text
totalConnected >= 1
connectedness = 1
gossipBlocks = true
~~~

현재 topology에서는 Pi4가 Pi5 Sequencer를 static peer로 사용한다.

---

# 6. Pi4 P2P Statistics 확인

~~~bash
curl -s \
  -H 'Content-Type: application/json' \
  --data '{
    "jsonrpc":"2.0",
    "method":"opp2p_peerStats",
    "params":[],
    "id":1
  }' \
  http://127.0.0.1:8547 \
| jq
~~~

정상 예:

~~~text
connected     = 1
blocksTopic   = 1
blocksTopicV2 = 1
blocksTopicV3 = 1
blocksTopicV4 = 1
banned        = 0
~~~

`connected=0`이면 peer connection 문제다.

---

# 7. Pi4 Sync Status 확인

Pi4:

~~~bash
curl -s \
  -H 'Content-Type: application/json' \
  --data '{
    "jsonrpc":"2.0",
    "method":"optimism_syncStatus",
    "params":[],
    "id":1
  }' \
  http://127.0.0.1:8547 \
| jq '{
    unsafe_l2: .result.unsafe_l2.number,
    safe_l2: .result.safe_l2.number,
    finalized_l2: .result.finalized_l2.number,
    current_l1: .result.current_l1.number,
    head_l1: .result.head_l1.number
  }'
~~~

P2P가 정상이라고 해서 반드시:

~~~text
unsafe = safe
~~~

여야 하는 것은 아니다.

오히려 정상적인 Sequencer 운영 중에는:

~~~text
unsafe > safe
~~~

일 수 있다.

unsafe는 P2P를 통해 먼저 전달되고,
safe는 L1 batch derivation 이후 따라오기 때문이다.

---

# 8. Sequencer / Verifier Unsafe Head 비교

Pi5와 Pi4에서 각각 다음 명령을 실행한다.

~~~bash
curl -s \
  -H 'Content-Type: application/json' \
  --data '{
    "jsonrpc":"2.0",
    "method":"optimism_syncStatus",
    "params":[],
    "id":1
  }' \
  http://127.0.0.1:8547 \
| jq '{
    unsafe: .result.unsafe_l2.number,
    safe: .result.safe_l2.number,
    finalized: .result.finalized_l2.number
  }'
~~~

정상 steady-state 예:

~~~text
Pi5 unsafe = N
Pi4 unsafe = N 또는 N-1 ~ 수 블록 차이
~~~

Pi4 unsafe가 Pi5보다 수백~수천 블록 지속적으로 뒤처지면 P2P propagation을 점검해야 한다.

---

# 9. 실시간 Pi4 Unsafe 모니터링

Pi4:

~~~bash
while true; do
  TS="$(date '+%Y-%m-%d %H:%M:%S %Z')"

  curl -s \
    -H 'Content-Type: application/json' \
    --data '{
      "jsonrpc":"2.0",
      "method":"optimism_syncStatus",
      "params":[],
      "id":1
    }' \
    http://127.0.0.1:8547 \
  | jq -r --arg ts "$TS" '
    "\($ts) | unsafe=\(.result.unsafe_l2.number) | safe=\(.result.safe_l2.number) | gap=\(.result.unsafe_l2.number - .result.safe_l2.number)"
  '

  sleep 10
done
~~~

`Ctrl+C`로 종료한다.

---

# 10. P2P Payload 처리 오류 확인

Pi4:

~~~bash
cd /opt/opstack/compose

docker compose logs \
  --since=5m \
  op-node \
| grep -Ei \
'unsafe payload|node is syncing|p2p|gossip|peer|error|failed' \
| tail -100
~~~

다음 오류가 P2P 활성화 직후 잠시 발생할 수 있다.

~~~text
failed to insert unsafe payload
updated forkchoice, but node is syncing
~~~

P2P 설정 전부터 기존 synchronization gap이 있었다면
최신 payload의 parent block이 아직 존재하지 않아 발생할 수 있다.

이 경우 즉시 P2P를 비활성화하지 않는다.

먼저:

- Pi4 safe head가 계속 증가하는지
- 기존 gap이 감소하는지
- P2P peer가 계속 connected 상태인지

확인한다.

기존 gap이 연결된 후 unsafe head가 최신 head로 따라붙으면 정상이다.

---

# 11. End-to-End P2P 검증

P2P 연결 여부만으로는 충분하지 않다.

실제 신규 L2 transaction을 보내고 Pi4에서 얼마나 빨리 확인되는지 측정한다.

Verifier:

~~~text
eth_getTransactionByHash
~~~

가 수초 내 transaction object를 반환하면 정상적인 unsafe propagation이 이루어지고 있는 것이다.

Mintaray에서 측정한 정상 기준 사례:

~~~text
Pi4 Verifier RPC  = 4.581 seconds
Public Explorer   = 5.785 seconds
~~~

정확한 시간은 네트워크 및 block timing에 따라 달라질 수 있으므로
위 수치는 절대적인 SLA가 아니라 정상 동작 확인을 위한 reference다.

---

# 12. 장애 판정

## Case A — Peer 자체가 없음

~~~text
totalConnected = 0
~~~

확인:

1. Pi5 op-node 실행 상태
2. Pi5 9222 TCP listener
3. Pi4 → Pi5:9222 연결
4. static peer 설정
5. Pi5 Peer ID
6. P2P identity / peerstore 상태

---

## Case B — Peer는 있지만 unsafe가 진행하지 않음

~~~text
connected = 1
gossipBlocks = true

하지만 Pi4 unsafe head 정체
~~~

확인:

1. Pi4 op-node logs
2. Engine syncing 여부
3. 기존 unsafe/safe gap
4. Pi4 op-reth 상태
5. Pi5 Sequencer 실제 block 생성 여부

---

## Case C — Pi4에는 TX가 있지만 Explorer에는 없음

Pi4:

~~~text
eth_getTransactionByHash = FOUND
~~~

Explorer:

~~~text
404
~~~

이면 P2P 문제가 아니라 Blockscout indexing 경로를 점검한다.

확인:

- Pi4 op-reth head
- Blockscout max indexed block
- Blockscout DB transaction 존재 여부
- Blockscout backend API
- Public Explorer API

---

# 13. 복구 원칙

P2P 장애 발생 시 무조건 DB를 삭제하거나 node를 재구축하지 않는다.

점검 순서:

~~~text
1. Container status
       ↓
2. P2P listener
       ↓
3. Network reachability
       ↓
4. Peer connection
       ↓
5. Gossip status
       ↓
6. unsafe head convergence
       ↓
7. Execution engine 상태
       ↓
8. 필요 시 op-node만 재시작
~~~

DB 삭제나 chain 재구축은 마지막 수단으로 취급한다.

---

# 14. 보안 주의사항

점검 과정에서 다음 값을 출력하거나 문서에 기록하지 않는다.

- Unsafe Block Signer private key
- Batcher private key
- Proposer private key
- Challenger credential
- JWT 실제 값
- L1 RPC API key

다음 정보는 공개되어도 secret이 아니다.

- Peer ID
- 사설 LAN IP
- P2P port
- service port
- block number
- public contract address

`docker inspect`의 전체 environment 출력은 secret을 포함할 수 있으므로 사용하지 않는다.

필요한 항목만 필터링하여 확인한다.
