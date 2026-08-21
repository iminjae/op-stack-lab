# Incident 001 — Explorer 트랜잭션 반영 지연

## 요약

Mintaray L2에서 Sequencer가 트랜잭션을 정상적으로 블록에 포함했음에도,
Public Blockscout Explorer에서는 해당 트랜잭션이 약 20~30분 뒤에야 조회되는 문제가 발생했다.

Pi4 Verifier의 op-node가 Sequencer의 최신 unsafe block을 P2P로 전달받지 못하고
L1 derivation에만 의존하고 있는 것이 원인이었다.

Pi5 Sequencer와 Pi4 Verifier 사이에 Private LAN 기반 static P2P 연결을 구성한 결과:

- Pi4 Verifier RPC 반영: 4.581초
- Public Blockscout Explorer 반영: 5.785초

safe 및 finalized 상태는 기존 L1 derivation 기반 검증 구조를 그대로 유지한다.

## 증상

Sequencer에서는 신규 트랜잭션이 즉시 확인되었지만,
Pi4 Verifier에서는 동일한 트랜잭션이 바로 조회되지 않았다.

Verifier에서:

~~~text
eth_getTransactionByHash
→ null
~~~

이 반환되었고, 해당 L2 블록이 L1을 통해 derivation된 이후에야 조회할 수 있었다.

## 원인

Pi4와 Pi5의 op-node 설정에:

~~~text
--p2p.disable=true
~~~

가 적용되어 있었다.

따라서 Pi4 Verifier는 Pi5 Sequencer가 생성한 최신 unsafe block을
P2P gossip으로 직접 받을 수 없었다.

기존 흐름:

~~~text
Transaction
    ↓
Pi5 Sequencer
    ↓
op-batcher
    ↓
Ethereum Sepolia
    ↓
Pi4 L1 Derivation
    ↓
Blockscout
    ↓
Explorer
~~~

결과적으로 Explorer 표시가 L1 derivation latency에 의존하여 약 20~30분 지연되었다.

## 조치

Pi5와 Pi4 사이에 OP Stack P2P 연결을 구성했다.

Pi5 Sequencer:

- P2P 활성화
- TCP/UDP 9222
- Persistent P2P identity
- Persistent peerstore
- Discovery 비활성화
- Unsafe Block Signer 적용
- Private LAN에만 P2P listener 노출

Pi4 Verifier:

- P2P 활성화
- Discovery 비활성화
- Persistent P2P identity / peerstore
- Pi5 Sequencer를 static peer로 지정

구조:

~~~text
                  unsafe
Pi5 Sequencer ─── P2P ───▶ Pi4 Verifier
      │                         │
      │                         │
      └── Batch → Sepolia ──────┘
                     │
                     ▼
               safe / finalized
~~~

P2P는 low-latency unsafe block propagation을 담당한다.

safe 및 finalized 상태는 기존과 동일하게 L1 derivation을 통해 검증된다.

## P2P 검증

Pi4에서:

~~~text
totalConnected = 1
connected      = 1
gossipBlocks   = true
~~~

를 확인했다.

또한 P2P 활성화 이후 Pi4의 unsafe head가 safe head보다 먼저 진행하는 것을 확인했다.

~~~text
unsafe=128688 | safe=128634
unsafe=128994 | safe=128710
unsafe=129080 | safe=129019
~~~

## End-to-End 검증

기존 synchronization gap이 해소된 후 새로운 L2 트랜잭션으로 측정했다.

Transaction:

~~~text
0xf99d73278bc4ab73d4314f474b44eeaef2cd07a06e6fbc38f85dde1dd0b6d63d
~~~

Block:

~~~text
129469
~~~

측정 결과:

| 구간 | 반영 시간 |
| --- | ---: |
| Pi4 Verifier RPC | 4.581초 |
| Public Blockscout Explorer | 5.785초 |

Blockscout 자체의 추가 반영 시간은 약:

~~~text
5.785 - 4.581 = 1.204초
~~~

였다.

## Before / After

Before:

~~~text
TX
↓
Sequencer
↓
L1 Batch
↓
Sepolia
↓
Verifier Derivation
↓
Blockscout

Explorer 반영: 약 20~30분
~~~

After:

~~~text
TX
↓
Sequencer
↓ P2P
Verifier
↓
Blockscout

Verifier RPC: 4.581초
Explorer:     5.785초
~~~

## 운영 관점에서 얻은 점

1. Explorer 지연과 Sequencer 실행 지연은 별개로 분석해야 한다.
2. unsafe, safe, finalized head를 각각 관측해야 한다.
3. Explorer가 Verifier를 데이터 소스로 사용하는 경우 low-latency unsafe propagation 경로가 필요하다.
4. Peer 연결 여부뿐 아니라 실제 block gossip 여부까지 확인해야 한다.
5. 기존 synchronization gap은 P2P 활성화 직후의 검증 결과를 왜곡할 수 있다.
6. 인프라 개선 효과는 사용자 경로까지 End-to-End latency로 검증해야 한다.

## 후속 작업

- P2P Health Check Runbook 작성
- Sequencer / Verifier unsafe head 비교 절차 문서화
- Node Recovery Runbook 작성
- Git + Ansible 기반 node configuration 관리 유지
