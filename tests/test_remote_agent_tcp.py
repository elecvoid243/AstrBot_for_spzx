"""Three real processes exercise the shared authority through TCP and NAT."""

import asyncio
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest


@pytest.mark.asyncio
async def test_real_three_instance_budget_and_outbound_only_origin(tmp_path):
    """Keep one budget through branches, stricter hops, reverse sends and loss.

    Args:
        tmp_path: Separate databases, configuration and logs for all three processes.
    """
    root = Path(__file__).resolve().parents[1]
    ports, processes, logs = [], [], []
    for _ in range(3):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            ports.append(listener.getsockname()[1])
    bases = [f"http://127.0.0.1:{port}" for port in ports]
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        try:
            for index, port in enumerate(ports):
                log = (tmp_path / f"instance-{index}.log").open("w", encoding="utf-8")
                logs.append(log)
                processes.append(
                    await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        "tests.fixtures.remote_agent_worker",
                        "--port",
                        str(port),
                        "--root",
                        str(tmp_path / str(index)),
                        cwd=root,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW
                        if sys.platform == "win32"
                        else 0,
                    )
                )
            accounts = []
            for base in bases:
                for _ in range(240):
                    try:
                        response = await client.get(base + "/test/ready")
                        if response.status_code == 200:
                            accounts.append(response.json())
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail("TCP dashboard worker did not become ready")
            sessions = []
            for base in bases:
                sessions.append(
                    [
                        (
                            await client.post(
                                base + "/test/session", json={"title": f"Session {i}"}
                            )
                        ).json()["session_id"]
                        for i in range(7)
                    ]
                )
            bindings = {}
            for source, target in ((0, 1), (1, 2)):
                headers = {"Authorization": "Bearer " + accounts[source]["token"]}
                response = await client.post(
                    bases[source] + "/api/v1/chat/remote-connections",
                    headers=headers,
                    json={
                        "name": f"Direct {source} to {target}",
                        "base_url": bases[target],
                        "key": accounts[target]["key"],
                    },
                )
                assert response.status_code == 200, response.text
                binding = (
                    await client.get(
                        bases[source] + "/api/v1/chat/remote-connections",
                        headers=headers,
                    )
                ).json()["data"]["connections"][0]
                bindings[source, target] = {
                    "connection_id": binding["connection_id"],
                    "connection_revision": binding["revision"],
                }
            # B/C cannot initiate any HTTP request to origin A. A can poll B;
            # B can poll C. These are the only required control directions.
            for index in (1, 2):
                await client.post(
                    bases[index] + "/test/network", json={"blocked_ports": [ports[0]]}
                )
            for index in (0, 1):
                await client.post(
                    bases[index] + "/test/policy", json={"max_chain_sends": 15}
                )
            # Fourteen real accepted deliveries precede the concurrent race.
            for i in range(14):
                target = 1 if i % 2 == 0 else 2
                result = (
                    await client.post(
                        bases[0] + "/test/send",
                        json={
                            "source": sessions[0][0],
                            "session_id": sessions[1][target],
                            "text": f"Preceding send {i}",
                            **bindings[0, 1],
                        },
                    )
                ).json()
                assert result["status"] == "accepted", result
            raced = await asyncio.gather(
                client.post(
                    bases[0] + "/test/send",
                    json={
                        "source": sessions[0][0],
                        "session_id": sessions[0][3],
                        "text": "A sends at 14/15",
                    },
                ),
                client.post(
                    bases[1] + "/test/send",
                    json={
                        "source": sessions[1][1],
                        "session_id": sessions[1][3],
                        "text": "B sends at 14/15",
                    },
                ),
            )
            values = [row.json() for row in raced]
            assert sum(row["status"] == "accepted" for row in values) == 1, values
            assert "limit reached" in str(values)
            state = (await client.get(bases[0] + "/test/state")).json()
            assert state["sessions"][sessions[0][0]]["count"] == 15
            # Independent human source starts a new chain: four prior sends,
            # then joining C's limit five must bar a sixth on every branch.
            for i in range(4):
                result = (
                    await client.post(
                        bases[0] + "/test/send",
                        json={
                            "source": sessions[0][4],
                            "session_id": sessions[1][4],
                            "text": f"New chain send {i}",
                            **bindings[0, 1],
                        },
                    )
                ).json()
                assert result["status"] == "accepted", result
            await client.post(bases[2] + "/test/policy", json={"max_chain_sends": 5})
            fifth = (
                await client.post(
                    bases[1] + "/test/send",
                    json={
                        "source": sessions[1][4],
                        "session_id": sessions[2][0],
                        "text": "Fifth via only B to C",
                        **bindings[1, 2],
                    },
                )
            ).json()
            assert fifth["status"] == "accepted", fifth
            for index, source, target in (
                (0, sessions[0][4], sessions[0][5]),
                (1, sessions[1][4], sessions[1][5]),
                (2, sessions[2][0], sessions[2][1]),
            ):
                denied = (
                    await client.post(
                        bases[index] + "/test/send",
                        json={
                            "source": source,
                            "session_id": target,
                            "text": "Must not be sixth",
                        },
                    )
                ).json()
                assert "limit reached" in str(denied), denied
            for index in (1, 2):
                state = (await client.get(bases[index] + "/test/state")).json()
                assert ports[0] not in state["outbound_ports"], state
            # An indirectly known connection is never a capability on A.
            denied = (
                await client.post(
                    bases[0] + "/test/send",
                    json={
                        "source": sessions[0][6],
                        "session_id": sessions[2][0],
                        "text": "No indirect grant",
                        **bindings[1, 2],
                    },
                )
            ).json()
            assert denied["status"] == "error"
            # A new chain can return only after a separate reverse binding and
            # network access are explicitly provided. Rejoining A keeps its root.
            await client.post(bases[2] + "/test/policy", json={"max_chain_sends": 15})
            first = (
                await client.post(
                    bases[0] + "/test/send",
                    json={
                        "source": sessions[0][6],
                        "session_id": sessions[1][6],
                        "text": "Reverse chain first send",
                        **bindings[0, 1],
                    },
                )
            ).json()
            assert first["status"] == "accepted", first
            unbound = (
                await client.post(
                    bases[1] + "/test/send",
                    json={
                        "source": sessions[1][6],
                        "session_id": sessions[0][5],
                        "text": "No inferred reverse binding",
                        **bindings[0, 1],
                    },
                )
            ).json()
            assert unbound["status"] == "error", unbound
            headers = {"Authorization": "Bearer " + accounts[1]["token"]}
            response = await client.post(
                bases[1] + "/api/v1/chat/remote-connections",
                headers=headers,
                json={
                    "name": "Explicit reverse",
                    "base_url": bases[0],
                    "key": accounts[0]["key"],
                },
            )
            assert response.status_code == 200
            rows = (
                await client.get(
                    bases[1] + "/api/v1/chat/remote-connections", headers=headers
                )
            ).json()["data"]["connections"]
            reverse = next(row for row in rows if row["name"] == "Explicit reverse")
            await client.post(
                bases[1] + "/test/network", json={"allow_ports": [ports[0]]}
            )
            replied = (
                await client.post(
                    bases[1] + "/test/send",
                    json={
                        "source": sessions[1][6],
                        "session_id": sessions[0][5],
                        "text": "Explicit reverse second send",
                        "connection_id": reverse["connection_id"],
                        "connection_revision": reverse["revision"],
                    },
                )
            ).json()
            assert replied["status"] == "accepted", replied
            state = (await client.get(bases[0] + "/test/state")).json()
            assert state["sessions"][sessions[0][6]]["count"] == 2, state
            # Kill the sole authority. B retains its original proxy and refuses
            # new automatic sends rather than starting a separate local budget.
            processes[0].terminate()
            await asyncio.wait_for(processes[0].wait(), 10)
            denied = (
                await client.post(
                    bases[1] + "/test/send",
                    json={
                        "source": sessions[1][4],
                        "session_id": sessions[1][6],
                        "text": "No control fallback",
                    },
                )
            ).json()
            assert "control" in str(denied).lower(), denied
        finally:
            for process in processes:
                if process.returncode is None:
                    process.terminate()
            for process in processes:
                await asyncio.wait_for(process.wait(), 15)
            for log in logs:
                log.close()
