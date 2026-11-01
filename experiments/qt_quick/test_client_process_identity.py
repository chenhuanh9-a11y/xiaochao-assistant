"""Synthetic process identity only; no real process enumeration."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from client_process_identity import assistant_process_conflict


def process(pid, path=r'C:\Apps\Xingtu\星图.exe', user='PC\\alice'):
    return SimpleNamespace(pid=pid, info={'name':'星图.exe'}, exe=Mock(return_value=path), username=Mock(return_value=user))


class ProcessIdentityTests(unittest.TestCase):
    def test_name_is_insufficient_across_users_sessions_or_installations(self):
        current = process(1)
        for candidate, session in [(process(2,user='PC\\bob'),2), (process(2),3),
                                   (process(2,path=r'D:\Other\星图.exe'),2)]:
            with self.subTest(candidate=candidate,session=session):
                result = assistant_process_conflict(current=current, processes=[candidate],
                    session_id=lambda pid: 2 if pid == 1 else session)
                self.assertEqual(result.state,'absent')
        self.assertEqual(assistant_process_conflict(current=current,processes=[process(2)],session_id=lambda pid:2).state,'present')

    def test_inaccessible_candidate_or_owner_remains_unknown_not_known(self):
        candidate=process(2); candidate.exe.side_effect=PermissionError('fixture access denied')
        self.assertEqual(assistant_process_conflict(current=process(1),processes=[candidate],session_id=lambda pid:2).state,'unknown')
        current=process(1); current.username.side_effect=PermissionError('fixture access denied')
        self.assertEqual(assistant_process_conflict(current=current,processes=[],session_id=lambda pid:2).state,'unknown')

    def test_same_user_and_session_other_install_or_source_entry_does_not_block(self):
        result=assistant_process_conflict(current=process(1,path=r'C:\Python\python.exe'),
            processes=[process(2)],session_id=lambda pid:2)
        self.assertEqual(result.state,'absent')


if __name__=='__main__': unittest.main()
