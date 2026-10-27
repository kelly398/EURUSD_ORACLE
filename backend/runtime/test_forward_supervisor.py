"""One process owner, explicit source mode, unchanged legacy default."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,patch
from backend.runtime import market_data_supervisor as module
from backend.runtime.market_data_supervisor import WorkerSpec,MarketDataSupervisor,configured_workers
from backend.data_engine.test_clock_policy import policy,NOW


class SupervisorModeTests(unittest.TestCase):
    def test_no_policy_keeps_the_existing_two_workers(self):
        self.assertEqual(configured_workers(),module.WORKERS)
        self.assertEqual(len(MarketDataSupervisor().states),2)

    def test_policy_mode_has_one_forward_worker_and_no_legacy_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'policy.json';path.write_text(policy().model_dump_json(),encoding='utf-8')
            with patch('backend.data_engine.clock_policy.ClockPolicy.check_active'):
                workers=configured_workers(path)
            self.assertEqual(len(workers),1)
            self.assertEqual(workers[0].module,'backend.runtime.capture_forward_feed')
            self.assertEqual(workers[0].arguments,('--clock-policy',str(path.resolve()),'--continuous'))
            self.assertEqual(len(MarketDataSupervisor(workers).states),1)

    def test_worker_arguments_are_passed_without_a_shell(self):
        worker=WorkerSpec('utc_forward_feed','backend.runtime.capture_forward_feed',('--clock-policy','a path/policy.json','--continuous'))
        supervisor=MarketDataSupervisor((worker,))
        with patch.object(module.subprocess,'Popen',return_value=Mock(pid=123)) as spawn,patch('builtins.print'):
            supervisor._start_worker(supervisor.states[0])
        self.assertEqual(spawn.call_args.args[0][-3:],list(worker.arguments))
        self.assertNotIn('shell',spawn.call_args.kwargs)

    def test_duplicate_or_empty_worker_sets_are_rejected(self):
        worker=WorkerSpec('duplicate','a')
        for workers in ((),(worker,worker),(object(),)):
            with self.subTest(workers=workers),self.assertRaises(ValueError):MarketDataSupervisor(workers)

    def test_bad_policy_prevents_processes_from_starting(self):
        with patch.object(module,'parse_args',return_value=SimpleNamespace(clock_policy=Path('missing-policy.json'))),patch.object(module.subprocess,'Popen') as spawn,patch('builtins.print'):
            self.assertEqual(module.main(),2)
        spawn.assert_not_called()


if __name__=='__main__':unittest.main()
