# Incident 002 — L1 RPC Rate Limit으로 인한 Derivation Lag

## 요약

Mintaray OP Stack L2의 op-node가 Ethereum Sepolia L1 데이터를 가져오는 과정에서
외부 RPC Provider의 rate limit 및 daily quota에 도달하면서 derivation이 지속적으로 지연되는 문제가 발생했다.

주요 증상은 다음과 같았다.

- `429 Too Many Requests`
- `daily request limit reached`
- `context deadline exceeded`
- `current_l1`이 `head_l1`을 따라가지 못함
- derivation backlog 증가
- Sequencer가 L1 정보를 가져오지 못해 일시적으로 block sealing 실패

RPC Provider의 호출 제한과 op-node의 L1 요청 패턴을 분리해서 분석한 뒤,
동시성 및 request rate를 조절하고 충분한 quota를 가진 RPC plan으로 전환하여 backlog를 해소했다.

---

## 구성

Mintaray L2는 Ethereum Sepolia를 L1 settlement layer로 사용한다.

op-node는 L1 RPC를 통해 다음과 같은 데이터를 지속적으로 조회한다.

- L1 block headers
- receipts
- batch transaction data
- deposit events
- SystemConfig 관련 event
- derivation에 필요한 L1 chain data

따라서 L1 RPC의 성능과 quota는 L2 derivation 속도에 직접적인 영향을 준다.

---

## 증상

op-node 로그에서 다음 오류가 반복적으로 발생했다.

~~~text
429 Too Many Requests
~~~

또는:

~~~text
daily request limit reached
~~~

Sequencer에서도 L1 block 정보를 가져오지 못하면서 다음과 같은 temporary error가 발생했다.

~~~text
failed to find L1 block info by number
failed to fetch header by num
Engine failed temporarily
~~~

RPC가 정상적으로 응답하지 못하는 동안 op-node의 L1 derivation head가 뒤처졌다.

---

## 상태 진단

`optimism_syncStatus`를 사용해 다음 값을 분리해서 확인했다.

~~~text
head_l1
current_l1
safe_l1_origin
unsafe_l2
safe_l2
~~~

특히:

~~~text
scan_gap = head_l1 - current_l1
~~~

을 관찰하여 현재 op-node가 L1 최신 head와 얼마나 떨어져 있는지 확인했다.

실제 장애 기간에는 약 2만 블록 이상의 L1 scan gap이 관측되었다.

예:

~~~text
head_l1    ≈ 11,463,055
current_l1 ≈ 11,440,486
scan_gap   ≈ 22,569
~~~

이후 catch-up이 진행되면서:

~~~text
20,000
17,000
14,000
8,700
...
0
~~~

형태로 backlog가 감소하는 것을 확인했다.

### Scan gap은 작지만 safe chain이 뒤처지는 경우

이 사건은 RPC 제한으로 `current_l1` 자체가 지연된 사례다.
반면 [Incident 004](004-safe-chain-recovery.md)에서는 L1 scan gap이 작아도 safe origin 지연이 크게 관측됐다.
두 상황을 같은 원인으로 처리하지 않고 배치 제출·L1 확인·배치 처리·sequencing window·reorg를 구분한다.
진단 절차와 지표 단위는 [Safe chain Runbook](../runbooks/safe-chain-recovery.md)을 참고한다.
이 문서의 `scan_gap ≈ 4`는 당시 관측값이며 고정 정상 기준이 아니다.

---

## 원인

핵심 원인은 OP Stack 자체의 데이터 손상이 아니라
외부 L1 RPC Provider의 request quota 및 throughput 제한이었다.

op-node는 밀린 L1 데이터를 따라잡는 catch-up 과정에서 평상시보다 훨씬 많은 RPC 요청을 발생시킨다.

~~~text
Normal operation
L1 최신 head 근처에서 소량의 지속적인 요청

Catch-up
과거 L1 block / receipt / event를 빠르게 연속 조회
~~~

무료 또는 낮은 quota의 RPC Provider에서는 catch-up 과정에서 제한에 도달했다.

결과적으로:

~~~text
L1 RPC throttle / quota exhaustion
            ↓
op-node L1 request 실패
            ↓
current_l1 진행 정체
            ↓
derivation backlog 증가
            ↓
safe chain 진행 지연
~~~

이 발생했다.

---

## Provider 전환 과정

장애 분석 과정에서 여러 L1 RPC Provider를 사용했다.

### Alchemy

초기 catch-up에서는 비교적 빠른 속도로 backlog를 줄였지만
무료 사용량을 소진했다.

### Infura

유료 Dev plan으로 전환했으나 보수적인 request 설정에서
catch-up 속도가 충분하지 않았다.

사용했던 제한 설정 예:

~~~text
L1_MAX_CONCURRENCY=1
L1_RPC_RATE_LIMIT=1
L1_RPC_MAX_BATCH_SIZE=5
~~~

### QuickNode

Trial 환경에서는 daily quota에 도달하면서 다시 429가 발생했다.

이후 Build plan으로 전환하고 RPC 처리량에 맞게 op-node 설정을 조정했다.

예:

~~~text
L1_MAX_CONCURRENCY=4
L1_RPC_RATE_LIMIT=3
L1_RPC_MAX_BATCH_SIZE=10
~~~

그 결과 catch-up 속도가 개선되었다.

---

## RPC Provider 변경 시 관찰된 현상

L1 RPC Provider를 변경하고 op-node를 재시작했을 때
`current_l1`이 즉시 이전 위치에서 그대로 이어지지 않고
일부 L1 구간을 다시 scan하는 현상이 관찰되었다.

따라서:

~~~text
RPC 변경
→ op-node restart
→ 일부 L1 재스캔
→ 일시적으로 scan_gap 증가
~~~

가 발생할 수 있었다.

이 때문에 단순히 RPC Provider를 계속 교체하는 것은
오히려 catch-up 비용을 증가시킬 수 있다는 점을 확인했다.

---

## 조치

### 1. RPC 오류와 chain DB 문제 분리

다음 항목을 함께 확인했다.

- `optimism_syncStatus`
- op-node 429 count
- timeout count
- receipt fetch failure
- execution engine 상태
- current/head L1 차이

이를 통해 execution DB 손상이 아니라
L1 data availability 문제임을 분리했다.

### 2. L1 RPC request rate 조정

Provider quota에 맞게:

- max concurrency
- RPC rate limit
- batch size

를 조절했다.

### 3. 충분한 throughput을 가진 RPC plan 사용

catch-up 기간 동안 더 높은 quota의 RPC plan을 사용하여
누적 backlog를 해소했다.

### 4. backlog를 수치로 지속 관측

5분 주기로 다음 값을 출력해 catch-up 속도를 추적했다.

~~~text
timestamp
head_l1
current_l1
scan_gap
unsafe_l2
safe_l2
~~~

---

## 결과

RPC throttling을 제거하고 적절한 quota를 확보한 뒤
L1 derivation backlog가 지속적으로 감소했다.

최종적으로:

~~~text
scan_gap ≈ 4
~~~

수준까지 회복되어 정상적인 L1 head 추적 상태로 복귀했다.

이후 Sequencer / Batcher / Proposer를 정상 운영 상태로 복구했다.

---

## 운영 관점에서 얻은 점

1. L2 노드 운영에서 L1 RPC는 단순 외부 API가 아니라 핵심 인프라 dependency다.
2. 월간 quota뿐 아니라 daily limit, throughput, concurrency 제한을 함께 확인해야 한다.
3. Catch-up workload는 정상 운영 시의 RPC workload와 크게 다르다.
4. `head_l1`과 `current_l1` 차이를 통해 derivation backlog를 직접 수치화할 수 있다.
5. RPC Provider 변경 및 op-node restart는 일부 L1 재스캔 비용을 만들 수 있다.
6. 반복적인 Provider 교체보다 현재 backlog와 quota를 계산한 뒤 전략적으로 전환하는 것이 낫다.
7. 429 오류와 execution DB corruption을 분리해서 진단해야 불필요한 node rebuild를 피할 수 있다.

---

## 후속 작업

- L1 RPC 상태 점검 Runbook 작성
- RPC quota / 429 감시 절차 문서화
- op-node derivation lag 확인 명령 문서화
- Provider 변경 시 재스캔 가능성을 운영 절차에 반영
- Prometheus / Grafana를 통해 L1 derivation 상태 지속 관측
