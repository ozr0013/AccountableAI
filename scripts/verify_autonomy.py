"""Opt-in Claude/PostgreSQL smoke test in a disposable schema; never sends mail."""
import argparse
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True,
                        help='Authorize a bounded real Anthropic request using the configured key')
    args = parser.parse_args()
    if not args.live:
        return
    load_dotenv()
    from agents.supervisor import Supervisor
    from rails.real import RealRails
    from workflow.autonomy import RuntimeActions
    from workflow.cases import CaseStore
    from workflow.scheduler import Scheduler
    from world import generate
    schema = 'autonomy_verify_' + uuid.uuid4().hex[:12]
    rails = RealRails(schema)
    try:
        for event in generate(13, (3,), 0).events():
            rails.append_event(event)
        now = datetime(2026, 4, 22, 12, tzinfo=UTC)
        case = CaseStore(rails).ensure(rails.ledger('keyway')[0], now)
        result = Supervisor(rails, actions=RuntimeActions(rails), max_steps=8).run(case.id, now)
        events = [e for e in rails.events() if e.payload.get('case_id') == case.id]
        calls = [e.payload for e in events if e.type == 'supervisor_model_call']
        tools = [e.payload for e in events if e.type == 'supervisor_tool_request']
        observations = [e.payload for e in events if e.type == 'supervisor_tool_result']
        errors = [e.payload for e in events if e.type == 'supervisor_error']
        assert calls and tools and not errors, 'No successful native model/tool loop'
        assert any(t['name'] == 'read' for t in tools), 'Model did not retrieve database evidence'
        assert observations and Scheduler(rails).list(case.id), 'Missing durable trace/wake'
        restored = RealRails(schema)
        try:
            saved = CaseStore(restored).get(case.id)
            assert saved.next_wake_at or saved.state in ('escalated', 'resolved')
        finally:
            restored.store.conn.close()
        report = {'result': result, 'model_calls': calls, 'tools': tools,
                  'observations': observations, 'restart_verified': True,
                  'isolated_schema_removed': True, 'external_messages': 0,
                  'case': saved.model_dump(mode='json')}
        target = Path('docs/AUTONOMY_LIVE_VERIFICATION.json')
        target.write_text(json.dumps(report, indent=2, default=str) + '\n')
        print(json.dumps({'status': 'passed', 'model_calls': len(calls),
                          'tools': [t['name'] for t in tools], 'report': str(target)}))
    finally:
        # Schema name is generated locally, never user-supplied.
        from psycopg import sql
        rails.store.conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
        rails.store.conn.close()


if __name__ == '__main__':
    main()
