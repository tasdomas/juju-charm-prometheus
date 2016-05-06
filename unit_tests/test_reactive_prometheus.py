import os
import mock
import shutil
import tempfile
import unittest
import yaml

from reactive import prometheus as react_prom
from charmhelpers.core import unitdata
from charmhelpers.core.templating import render
from charms.reactive import bus

os.environ['JUJU_UNIT_NAME'] = 'prometheus'
os.environ['CHARM_DIR'] = '..'

fixed_scrape_config = {'job_name': 'prometheus', 'target_groups':
                       [{'targets': ['localhost:9090']}]}


class ReactInterfaceMock(object):
    def __init__(self, serv_dict):
        self.serv_dict = serv_dict

    def services(self):
        srvs = []
        for name, hostports in self.serv_dict.items():
            srvs.append({
                'service_name': name,
                'hosts': [{'hostname': h, 'port': p} for h, p in hostports],
            })
        return srvs


class SimpleConfigMock(dict):
    def __init__(self, *arg, **kw):
        super(SimpleConfigMock, self).__init__(*arg, **kw)
        self._changed_dict = {}
        self._changed_default = True

    def changed(self, key):
        return self._changed_dict.get(key, self._changed_default)

    def set_changed(self, changed_dict):
        self._changed_dict.update(changed_dict)


@mock.patch('os.chown')
@mock.patch('os.fchown')
@mock.patch('reactive.prometheus.hookenv.open_port')
@mock.patch('reactive.prometheus.hookenv.close_port')
@mock.patch('reactive.prometheus.host.service_restart')
@mock.patch('reactive.prometheus.host.service_running')
@mock.patch('reactive.prometheus.data_changed')
@mock.patch('reactive.prometheus.validate_config')
@mock.patch('reactive.prometheus.hookenv.unit_get')
@mock.patch('reactive.prometheus.hookenv.config')
class TestPrometheusContext(unittest.TestCase):
    def setUp(self):
        super(TestPrometheusContext, self).setUp()
        self._init_tempdir_and_filenames()
        self._init_load_config_yml_defaults()
        self._init_override_render_for_nonroot()

    def _init_tempdir_and_filenames(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.maxDiff = None
        self.prom_yml = os.path.join(self.dir, 'test_prometheus.yml')
        self.prom_def = os.path.join(self.dir, 'test_etc_default_prometheus')
        self.prom_custom_rules = os.path.join(self.dir, 'custom.rules')
        react_prom.PROMETHEUS_YML = self.prom_yml
        react_prom.PROMETHEUS_DEF = self.prom_def
        react_prom.CUSTOM_RULES_PATH = self.prom_custom_rules
        # ugly hack, to avoid carrying global unitdata state across tests
        os.environ['UNIT_STATE_DB'] = os.path.join(self.dir, '.unit-state.db')
        unitdata._KV = None

    def _init_load_config_yml_defaults(self):
        # create def_config with parsed config.yaml defaults
        with open("../config.yaml") as fh:
            config_yaml = yaml.safe_load(fh)
            self.def_config = SimpleConfigMock({k: v["default"] for k, v in
                                                config_yaml["options"].items()
                                                if v.get("default")})

    def _init_override_render_for_nonroot(self):
        # need to override render() for 644 perms (ie allow overriding target
        # when running as non-root)
        def render_and_chmod(*args, **kwargs):
            render(*args, **kwargs)
            os.chmod(kwargs['target'], 0o664)
        self.mock_render = mock.patch('reactive.prometheus.render',
                                      side_effect=render_and_chmod)
        self.mock_render.start()
        self.addCleanup(self.mock_render.stop)

    @mock.patch('reactive.prometheus.set_state')
    def test_update_prometheus_targets(self,
                                       mock_set_state,
                                       mock_hookenv_config,
                                       mock_unit_get,
                                       mock_validate_config,
                                       *args):
        mock_hookenv_config.return_value = self.def_config
        mock_unit_get.return_value = 'localhost'
        srv1_name = 'foo'
        srv1_hostport1 = ['foohost1', 'fooport1']
        srv1_hostport2 = ['foohost2', 'fooport2']
        srv2_name = 'bar'
        srv2_hostport1 = ['barhost1', 'barport1']
        srvs_mock = {srv1_name: [srv1_hostport1, srv1_hostport2],
                     srv2_name: [srv2_hostport1]}
        req_mock = ReactInterfaceMock(srvs_mock)
        react_prom.update_prometheus_targets(req_mock)
        mock_set_state.assert_called_once_with('prometheus.do-check-reconfig')
        react_prom.write_prometheus_config_yml()
        react_prom.write_prometheus_config_def()
        mock_validate_config.assert_called_once_with()
        yaml_content = yaml.safe_load(open(self.prom_yml))
        exp_list = [
            fixed_scrape_config,
            {'job_name': srv1_name,
             'target_groups': [{'labels': {'group': 'promoagents-juju'},
                                'targets': [
                                    '{0}:{1}'.format(*srv1_hostport1),
                                    '{0}:{1}'.format(*srv1_hostport2)]}]
             },
            {'job_name': srv2_name,
             'target_groups': [{'labels': {'group': 'promoagents-juju'},
                                'targets': [
                                    '{0}:{1}'.format(*srv2_hostport1)]}]
             },
        ]
        self.assertListEqual(sorted(yaml_content['scrape_configs'],
                                    key=lambda x: x['job_name']),
                             sorted(exp_list,
                                    key=lambda x: x['job_name']))

        return

    @mock.patch('reactive.prometheus.set_state')
    def test_update_prometheus_no_targets(self,
                                          mock_set_state,
                                          mock_hookenv_config,
                                          mock_unit_get,
                                          mock_validate_config,
                                          *args):
        mock_hookenv_config.return_value = self.def_config
        mock_unit_get.return_value = 'localhost'
        react_prom.update_prometheus_no_targets()
        mock_set_state.assert_called_once_with('prometheus.do-check-reconfig')
        react_prom.write_prometheus_config_yml()
        react_prom.write_prometheus_config_def()
        mock_validate_config.assert_called_once_with()
        yaml_content = yaml.safe_load(open(self.prom_yml))
        exp_dict = fixed_scrape_config
        self.assertDictEqual(yaml_content['scrape_configs'][0], exp_dict)

    def test_update_prometheus_static_targets(self,
                                              mock_hookenv_config,
                                              mock_unit_get,
                                              mock_validate_config,
                                              *args):
        mock_hookenv_config.return_value = {
            'scrape-interval': '15',
            'evaluation-interval': '15',
            'static-targets': 'foo:1234 , bar:5678 ',
        }
        mock_unit_get.return_value = 'localhost'
        react_prom.update_prometheus_no_targets()
        react_prom.write_prometheus_config_yml()
        react_prom.write_prometheus_config_def()
        mock_validate_config.assert_called_once_with()
        yaml_content = yaml.safe_load(open(self.prom_yml))
        exp_dict = {'job_name': 'static-targets', 'target_groups':
                    [{'labels': {'group': 'promoagents-static'},
                      'targets': ['foo:1234', 'bar:5678']}]}

        self.assertDictEqual(yaml_content['scrape_configs'][1], exp_dict)

    def test_update_prometheus_no_alertmanager(self,
                                               mock_hookenv_config,
                                               *args):
        mock_hookenv_config.return_value = self.def_config
        react_prom.update_prometheus_no_alertmanager()
        react_prom.write_prometheus_config_yml()
        react_prom.write_prometheus_config_def()
        # Verify no etc/default/prometheus file created
        self.assertFalse(os.path.exists(self.prom_def))

    def test_update_prometheus_alertmanager(self,
                                            mock_hookenv_config,
                                            *args):
        mock_hookenv_config.return_value = self.def_config
        srv1_name = 'alertmanager'
        srv1_hostport1 = ['foohost1', 'fooport1']
        srv1_hostport2 = ['foohost2', 'fooport2']
        srv1_mock = {srv1_name: [srv1_hostport1, srv1_hostport2]}
        req_mock = ReactInterfaceMock(srv1_mock)
        react_prom.update_prometheus_alertmanager(req_mock)
        react_prom.write_prometheus_config_yml()
        react_prom.write_prometheus_config_def()
        # Verify etc/default/prometheus has -alertmanager.url set to
        # last alertmanager in related services
        with open(self.prom_def) as fh:
            self.assertRegexpMatches(
                fh.readline(),
                'ARGS.*-alertmanager.url http://{0}:{1}.*'.format(
                    *srv1_hostport2)
            )

    @mock.patch('reactive.prometheus.set_state')
    def test_install_packages_conditionally_called(self,
                                                   mock_set_state,
                                                   mock_hookenv_config,
                                                   mock_unit_get,
                                                   mock_validate_config,
                                                   *args):
        config = self.def_config
        mock_hookenv_config.return_value = self.def_config
        # No configs changed, expect no prometheus-do-install
        config.set_changed({'install_sources': False, 'install_keys': False})
        react_prom.check_reconfig_prometheus()
        self.assertFalse(mock.call('prometheus.do-install')
                         in mock_set_state.call_args_list)
        # Mock some configs changed, expect prometheus-do-install
        mock_set_state.reset_mock()
        config.set_changed({'install_sources': True, 'install_keys': False})
        react_prom.check_reconfig_prometheus()
        print (mock_set_state.call_args_list)
        self.assertTrue(mock.call('prometheus.do-install')
                        in mock_set_state.call_args_list)

    def test_other_configs(self,
                           mock_hookenv_config,
                           *args):
        config = self.def_config
        fake_ext_url = 'http://foo:1234/bar'
        fake_rules = 'BLAH\nBLEH!'
        config.update({
            'external_url': fake_ext_url,
            'custom-rules': fake_rules,
        })
        mock_hookenv_config.return_value = config
        react_prom.update_prometheus_no_targets()
        react_prom.write_prometheus_config_yml()
        react_prom.write_prometheus_config_def()
        # Verify etc/default/prometheus has -web.external-url added
        with open(self.prom_def) as fh:
            self.assertRegexpMatches(
                fh.readline(),
                'ARGS.*-web.external-url {}.*'.format(fake_ext_url))
        # Verify custom.rules file created with expected content
        with open(self.prom_custom_rules) as fh:
            self.assertEqual(fh.read(), fake_rules)

    @mock.patch('reactive.prometheus.subprocess.check_output')
    @mock.patch('reactive.prometheus.set_state')
    def test_configure_storage(self,
                               mock_set_state,
                               mock_subprocess_check_output,
                               mock_hookenv_config,
                               mock_unit_get,
                               mock_validate_config,
                               *args):
        mock_hookenv_config.return_value = self.def_config
        fake_storage_loc = '/srv/foo/bar'
        mock_subprocess_check_output.return_value = fake_storage_loc.encode()
        react_prom.configure_storage()
        mock_set_state.assert_called_with('prometheus.do-check-reconfig')
        react_prom.write_prometheus_config_yml()
        react_prom.write_prometheus_config_def()
        # Verify etc/default/prometheus has -storage.local.path w/expected value
        with open(self.prom_def) as fh:
            self.assertRegexpMatches(
                fh.readline(),
                'ARGS.*-storage.local.path {}.*'.format(fake_storage_loc))

    @mock.patch('charms.reactive.bus.SourceFileLoader')
    @mock.patch('reactive.prometheus.basenode')
    @mock.patch('reactive.prometheus.fetch')
    @mock.patch('reactive.prometheus.install_packages')

    # Simulate a full run, from zero (actually, 'basenode.complete')
    # to final state, verify we end calling service_restarted('prometheus')
    def test_started_after_full_run(self,
                                    mock_install_packages,
                                    mock_fetch,
                                    mock_basenode,
                                    mock_sourcefileloader,
                                    mock_hookenv_config,
                                    mock_hookenv_unit_get,
                                    mock_validate_config,
                                    mock_data_changed,
                                    mock_service_running,
                                    mock_service_restarted,
                                    *args):

        mock_hookenv_config.return_value = self.def_config
        mock_data_changed.return_value = True
        bus.set_state('basenode.complete')
        bus.dispatch()
        mock_install_packages.called_once_with()
        mock_service_running.assert_called_with('prometheus')
        mock_service_restarted.assert_called_with('prometheus')
