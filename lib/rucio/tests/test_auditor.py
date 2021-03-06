'''
  Copyright European Organization for Nuclear Research (CERN)

 Licensed under the Apache License, Version 2.0 (the "License");
 You may not use this file except in compliance with the License.
 You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

 Authors:
 - Vincent Garonne,  <vincent.garonne@cern.ch> , 2017
 - Dimitrios Christidis <dimitrios.christidis@cern.ch>, 2018
 - Hannes Hansen <hannes.jakob.hansen@cern.ch>, 2019

 PY3K COMPATIBLE
'''

from __future__ import division
from datetime import datetime
from datetime import timedelta
from nose.tools import eq_
from nose.tools import ok_
from rucio.daemons import auditor

import bz2
import collections
import mock
import multiprocessing
import os
import tempfile


def test_auditor_guess_replica_info():
    tests = {
        'foo': (None, 'foo'),
        'foo/bar': ('foo', 'bar'),
        'foo/bar/baz': ('foo', 'baz'),
        'user': (None, 'user'),
        'user/foo': ('user', 'foo'),
        'user/foo/bar': ('user.foo', 'bar'),
        'group': (None, 'group'),
        'group/foo': ('group', 'foo'),
        'group/foo/bar': ('group.foo', 'bar'),
    }
    for input_, output in tests.items():
        eq_(auditor.guess_replica_info(input_), output)


class TestBz2CompressFile(object):

    def setup(self):
        self.source = tempfile.mktemp()
        self.destination = self.source + '.bz2'

    def teardown(self):
        for path in [self.source, self.destination]:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_auditor_bz2_compress_file(self):
        test_data = 'foo'
        with open(self.source, 'w') as f:
            f.write(test_data)

        destination = auditor.bz2_compress_file(self.source)

        eq_(destination, self.destination)
        ok_(os.path.exists(self.destination))
        ok_(not os.path.exists(self.source))
        with bz2.BZ2File(self.destination) as f:
            eq_(f.read().decode(), test_data)


def mock_fn_wrapper(return_value):
    calls = []

    def mock_fn(*args, **kwargs):
        calls.append({
            'args': args,
            'kwargs': kwargs,
        })
        return return_value

    return mock_fn, calls


date = datetime.strptime('01-01-2015', '%d-%m-%Y')
fake_gfal_download, fake_gfal_download_calls = mock_fn_wrapper(('', date))
fake_rrd_download, fake_rrd_download_calls = mock_fn_wrapper('')
fake_consistency_dump, fake_consistency_dump_calls = mock_fn_wrapper('')


@mock.patch('rucio.common.dumper.consistency.Consistency.dump', side_effect=fake_consistency_dump)
@mock.patch('rucio.daemons.auditor.hdfs.ReplicaFromHDFS.download', side_effect=fake_rrd_download)
@mock.patch('rucio.daemons.auditor.srmdumps.download_rse_dump', side_effect=fake_gfal_download)
def test_auditor_download_dumps_with_expected_dates(mocked_srmdumps, mocked_hdfs, mocked_auditor_consistency):
    tmp_dir = tempfile.mkdtemp()

    auditor.consistency('RSENAME', timedelta(days=3), None, cache_dir=tmp_dir, results_dir=tmp_dir)

    eq_(
        fake_rrd_download_calls[0]['args'][1],
        date.strptime('29-12-2014', '%d-%m-%Y')
    )

    eq_(
        fake_rrd_download_calls[1]['args'][1],
        date.strptime('04-01-2015', '%d-%m-%Y')
    )


def mocked_auditor_consistency(rse, delta, configuration, cache_dir, results_dir):
    if rse == 'RSE_WITH_EXCEPTION':
        raise Exception
    elif rse == 'RSE_SHOULD_WORK':
        pass
    else:
        return 1 / 0


@mock.patch('rucio.daemons.auditor.consistency', side_effect=mocked_auditor_consistency)
def test_auditor_check_survives_failures_and_queues_failed_rses(mock_auditor):
    queue = multiprocessing.Queue()
    retry = multiprocessing.Queue()
    queue.put(('RSE_WITH_EXCEPTION', 1))
    queue.put(('RSE_SHOULD_WORK', 1))
    queue.put(('RSE_WITH_ERROR', 1))
    wr_pipe = collections.namedtuple('FakePipe', ('send', 'close'))(
        lambda _: None,
        lambda: None,
    )

    class MockMultiProcessing():
        def is_set(self):
            return queue.empty()
    terminate = MockMultiProcessing()
    auditor.check(queue, retry, terminate, wr_pipe, None, None, 3, False)

    ok_(queue.empty())
    eq_(retry.get(), ('RSE_WITH_EXCEPTION', 0))
    eq_(retry.get(), ('RSE_WITH_ERROR', 0))
    ok_(retry.empty())
