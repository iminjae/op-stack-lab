# Runbook — op-node 버전·하드포크 대응

## 목적

Mintaray의 Pi5 Sequencer와 Pi4 Verifier에서 op-node 초기화 실패 또는 L1 / L2 하드포크 대응 시
버전·설정·실행 상태를 대조하고 호스트별로 변경과 복구를 검증한다.
Ethereum Sepolia 기반 개인 테스트 L2를 대상으로 한다.

아래 명령은 운영자가 점검할 때 사용할 읽기 전용 예시다. 이번 문서 작업에서는 실행하지 않았다.
호스트 명령은 대상 Pi의 Bash에서 실행하며 `docker compose`, `jq`, `curl`이 필요하다.
로그는 로컬에서 확인하고 공유 전 credential 포함 URL, private key, JWT 등을 제거한다.
전체 `docker inspect`, 환경변수 덤프, `.env` 출력, 값을 모두 펼치는 `docker compose config` 출력은 피한다.

---

# 1. 선언된 이미지와 실행 중 이미지 비교

2026-10-08 문서 작성 시 저장소 선언:

| 서비스 | Pi5 | Pi4 |
| --- | --- | --- |
| op-node | v1.19.3 | v1.19.3 |
| op-reth | v2.4.0 | v2.4.0 |
| op-batcher | v1.16.11 | 해당 없음 |
| op-proposer | v1.10.0 | 해당 없음 |
| op-challenger | 해당 없음 | v1.9.4 |

이 표는 권장 버전 목록이 아니다. [Incident 003](../incidents/003-op-node-l1-block-hash-mismatch.md)의
Pi5 실행 기록은 v1.19.8이며 저장소 선언과 다르다. 현재 두 호스트의 실제 상태는 따로 확인한다.

저장소 루트에서 이미지 선언만 확인한다.

~~~bash
rg -n '^    image:' nodes/pi5/opstack/compose.yml nodes/pi4/opstack/compose.yml
~~~

대상 Pi에서 실행 이미지 참조, 이미지 ID, 프로세스 상태만 조회한다.

~~~bash
cd /opt/opstack/compose
TZ=Asia/Seoul date '+%Y-%m-%d %H:%M:%S %Z'
docker compose ps -a op-node
node_id=$(docker compose ps -a -q op-node)
if [ -n "$node_id" ]; then
  docker inspect --format 'image={{.Config.Image}} image_id={{.Image}} status={{.State.Status}} restarts={{.RestartCount}}' "$node_id"
else
  echo 'op-node container not found' >&2
fi
~~~

서버의 `compose.yml`과 `compose.override.yaml`에서 이미지·포트·비밀이 아닌 플래그를 제한적으로 확인한다.
저장소 파일, 서버 선언, 컨테이너 실행 이미지가 일치하는지 비교한다.
다른 Compose 파일이나 override로 실행했다면 실제 적용 파일 목록도 기록한다.

# 2. L1 / L2 호환 요구 조건 확인

1. L1 Sepolia의 활성 하드포크와 예정 일정을 공식 안내에서 확인한다.
2. Mintaray L2의 실제 rollup config에 선언된 하드포크 순서·활성 시각을 읽기 전용으로 확인한다.
3. 목표 op-node 릴리스의 L1 / L2 지원 범위, op-reth 등 관련 클라이언트 요구 조건과 ARM64 이미지 지원을 확인한다.
4. 설정 검증 강화, 데이터 형식 변경, 되돌리기 제약이 있는지 변경 내역을 확인한다.

[공식 op-node v1.19.8 릴리스 노트](https://github.com/ethereum-optimism/optimism/releases/tag/op-node/v1.19.8)는
2026-10-08 확인 기준 Sepolia OP Stack 노드에 10월 6일 Glamsterdam 전 v1.19.5 이상 업데이트를 요구한다.
또한 일부 L2 하드포크 활성 시각·순서 검증이 강화됐으므로 custom rollup config의 호환성을 검토한다.
이는 v1.19.8이 향후 모든 하드포크에 호환된다는 뜻이 아니다. 변경 시점에 공식 조건을 다시 확인한다.
해시 검증 오류만으로 세부 원인을 확정하거나 rollup / genesis 설정을 임의 수정하지 않는다.

# 3. 변경 전 기록과 적용 범위

호스트별로 다음을 기록한다. secret 값 대신 보관 위치와 관리 책임만 남긴다.

- 기록 시각 (KST), 실행 이미지 태그·ID, 저장소 기준 revision, 설정 차이
- op-node / op-reth 상태, 초기화 오류 발췌
- [Sync 지표 조회](safe-chain-recovery.md)의 head / origin / gap 측정값
- Pi5: Batcher / Proposer 연결 및 최근 제출 상태, Sequencer block 진행
- Pi4: P2P 연결, unsafe 수렴, safe / finalized 진행, Challenger 상태
- 목표 버전, 영향받는 호스트·서비스, 검증 담당자와 중단 / 복원 조건

이미 프로세스가 종료되어 조회할 수 없는 항목은 실패 상태를 기록하고 정상값으로 채우지 않는다.

Pi5 변경은 block 생성과 Batcher / Proposer의 의존 RPC에 영향을 줄 수 있다.
Pi4 변경은 Verifier와 Explorer가 보는 상태 및 Challenger에 영향을 줄 수 있다.
두 호스트를 동시에 변경하지 않고 한 호스트 적용·검증 후 다음 호스트로 진행한다.
Pi5의 성공을 Pi4의 성공으로 간주하지 않는다.

# 4. 변경 적용과 확인

변경은 목표 버전과 설정 호환성을 검토한 뒤 기존 Git + Ansible 절차로 별도 수행한다.
배포 경로는 [Node Recovery](node-recovery.md)의 Compose 설정 변경 절차와
[배포 playbook](../../ansible/playbooks/deploy-opstack.yml)을 참고한다.
이번 Runbook은 확인되지 않은 업데이트 실행 명령을 사건 당시 명령처럼 재구성하지 않는다.

적용 직후 각 호스트에서:

1. 실행 이미지와 재시작 횟수를 다시 조회한다.
2. 초기화 성공과 같은 오류 반복 여부를 확인한다.
3. localhost `127.0.0.1:8547`의 `optimism_syncStatus` 응답과 JSON-RPC 오류 유무를 [공통 조회 명령](safe-chain-recovery.md)으로 확인한다.
4. 여러 시점의 unsafe / safe / finalized 및 L1 추적을 비교한다. finality·배치 주기를 고려하고 단일 응답만으로 완료하지 않는다.
5. Pi5 Batcher / Proposer의 op-node 연결과 역할 수행을 확인한다. Pi4는 P2P, 실행 계층, Challenger 및 Explorer 경로를 따로 확인한다.
6. [Monitoring Runbook](monitoring-no-data.md)으로 metrics 수집 회복도 확인한다.

호스트별 결과를 `확인됨 / 실패 / 미확인`으로 남긴다. RPC 응답이나 `up=1`만으로 롤업 전체 복구를 선언하지 않는다.

# 5. 롤백 판단

이전 이미지로 되돌리기 전에 **현재 L1 / L2 하드포크와 이전 버전의 호환성**을 먼저 확인한다.
이미 필수 하드포크가 활성화됐다면 이전 버전으로 되돌리는 것이 초기화 실패를 재현할 수 있다.
데이터 형식·설정 호환성, 관련 클라이언트와의 조합까지 확인되지 않으면 단순 버전 되돌리기를 수행하지 않는다.
지원되는 수정 버전 적용 또는 진단을 위한 중단 여부를 별도로 판단하고 DB 삭제로 해결하려 하지 않는다.

# 6. 긴급 변경 후 설정 원본 정합성

- 호스트에서 바꾼 비밀이 아닌 이미지·플래그를 저장소의 해당 Pi Compose / override와 대조한다.
- 검증된 최종 설정만 별도 변경으로 저장소에 반영하고 임시 복구 설정이 남았는지 확인한다.
- 기존 playbook의 Compose validation과 호스트별 적용을 거쳐 실행 상태를 재확인한다.
- 호스트 로컬 secret은 저장소로 복사하지 않는다.

이번 문서 반영은 Compose / Ansible을 변경하지 않는다.
v1.19.3 선언과 Pi5 v1.19.8 실행 기록 간 차이를 해소하기 전 기존 설정을 재배포하면 이전 버전으로 돌아갈 수 있으므로 먼저 대조한다.

## 관련 문서

- [Incident 003 — op-node 기동 중단](../incidents/003-op-node-l1-block-hash-mismatch.md)
- [Incident 004 — Safe chain 진행 지연](../incidents/004-safe-chain-recovery.md)
- [Node Recovery](node-recovery.md)
- [P2P Health Check](p2p-health-check.md)
