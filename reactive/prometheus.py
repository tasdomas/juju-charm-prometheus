import os
import pwd
import subprocess

from charmhelpers import fetch
from charmhelpers.core import host, hookenv, unitdata
from charmhelpers.core.templating import render
from charmhelpers.payload.execd import execd_preinstall
from charmhelpers.contrib.charmsupport import nrpe
from charms.reactive import (
    when, when_not, set_state, remove_state, is_state, hook
)
from charms.reactive.helpers import any_file_changed, data_changed

SVCNAME = 'prometheus'
PKGNAMES = ['prometheus']
PROMETHEUS_YML = '/etc/prometheus/prometheus.yml'
PROMETHEUS_DEF = '/etc/default/prometheus'
PROMETHEUS_YML_TMPL = 'prometheus.yml.j2'
PROMETHEUS_DEF_TMPL = 'etc_default_prometheus.j2'
CUSTOM_RULES_PATH = '/etc/prometheus/custom.rules'


@when_not('basenode.complete')
def basenode():
    execd_preinstall()
    set_state('basenode.complete')


def templates_changed(tmpl_list):
    return any_file_changed(['templates/{}'.format(x) for x in tmpl_list])


# TODO: once there's reactive support for storage hooks, convert off @hook()
@hook('metrics-{filesystem,block}-storage-attached')
def configure_storage():
    storage_path = subprocess.check_output(
        ['storage-get', 'location']).decode().strip()
    kv = unitdata.kv()
    kv.set('storage-path', storage_path)
    runtime_args('-storage.local.path', storage_path)
    set_state('storage.configured')
    set_state('prometheus.do-check-reconfig')


def set_datadir_perms():
    datadir = unitdata.kv().get('storage-path', False)
    if not datadir:
        # No juju storage attached, use defaults from package
        return
    users = [i for i in pwd.getpwall() if i.pw_name == 'nobody']
    if len(users) == 1:
        os.lchown(datadir, users[0].pw_uid, users[0].pw_gid)


@when('prometheus.do-install')
def install_packages():
    fetch.configure_sources()
    fetch.apt_update()
    fetch.apt_install(PKGNAMES)
    remove_state('prometheus.do-install')


def runtime_args(key=None, value=None):
    kv = unitdata.kv()
    args = kv.get('runtime_args', {})
    if key:
        args.update({key: value})
        kv.set('runtime_args', args)
    args_list = ['{} {}'.format(k, v) for k, v in args.items() if v]
    # sorted list is needed to avoid data_changed() false-positives
    return sorted(args_list)


def validate_config():
    subprocess.check_call(['promtool', 'check-config', PROMETHEUS_YML])


@when('prometheus.do-reconfig-yml')
def write_prometheus_config_yml():
    config = hookenv.config()
    target_jobs = unitdata.kv().get('target_jobs', [])
    scrape_jobs = unitdata.kv().get('scrape_jobs', [])

    # transform eg. 'h1:p1 ,  h2:p2' (string), to ['h1:p1', 'h2:p2'] (list)
    static_targets = None
    if config.get('static-targets'):
        static_targets = [x.strip()
                          for x in config.get('static-targets', '').split(',')]

    default_monitor_name = '{}-monitor'.format(hookenv.service_name())
    options = {
        'scrape_interval': config['scrape-interval'],
        'evaluation_interval': config['evaluation-interval'],
        'static_targets': static_targets,
        'private_address': hookenv.unit_get('private-address'),
        'monitor_name': config.get('monitor_name', default_monitor_name),
        'jobs': target_jobs,
        'scrape_jobs': scrape_jobs,
    }

    # custom-rules content must be passed verbatim with e.g.
    #   juju set prometheus custom-rules @my.rules
    if config.get('custom-rules'):
        custom_rules = config['custom-rules']
        with open(CUSTOM_RULES_PATH, 'w') as fh:
            fh.write(custom_rules)
        options['custom_rules_file'] = CUSTOM_RULES_PATH

    render(source=PROMETHEUS_YML_TMPL,
           target=PROMETHEUS_YML,
           context=options
           )
    validate_config()
    set_state('prometheus.do-restart')
    remove_state('prometheus.do-reconfig-yml')


def check_ports(new_port):
    kv = unitdata.kv()
    if kv.get('prometheus.port') != new_port:
        hookenv.open_port(new_port)
        if kv.get('prometheus.port'):  # Dont try to close non existing ports
            hookenv.close_port(kv.get('prometheus.port'))
        kv.set('prometheus.port', new_port)


@when('prometheus.do-reconfig-def')
def write_prometheus_config_def():
    config = hookenv.config()
    port = config.get('port', '9090')
    check_ports(port)
    if config.get('external_url', False):
        vars = {
            'private_address': hookenv.unit_get('private-address'),
            'public_address': hookenv.unit_get('public-address'),
            # prometheus default:
            'port': port,
        }
        runtime_args('-web.external-url',
                     config['external_url'].format(**vars))
    args = runtime_args()
    hookenv.log('runtime_args: {}'.format(args))
    if args:
        render(source=PROMETHEUS_DEF_TMPL,
               target=PROMETHEUS_DEF,
               context={'args': args},
               )
    set_state('prometheus.do-restart')
    remove_state('prometheus.do-reconfig-def')


@when_not('prometheus.started')
def setup_prometheus():
    if not is_state('basenode.complete'):
        hookenv.status_set('maintenance', 'Waiting for basenode to run')
        return
    hookenv.status_set('maintenance', 'Configuring software')
    set_datadir_perms()
    install_packages()
    set_state('prometheus.do-check-reconfig')


@when('prometheus.started')
def check_config():
    set_state('prometheus.do-check-reconfig')


@when('prometheus.do-check-reconfig')
def check_reconfig_prometheus():
    config = hookenv.config()
    target_jobs = unitdata.kv().get('target_jobs', [])
    scrape_jobs = unitdata.kv().get('scrape_jobs', [])
    args = runtime_args()
    install_opts = ('install_sources', 'install_keys')
    if any(config.changed(opt) for opt in install_opts):
        set_state('prometheus.do-install')
    if data_changed('prometheus.config', config):
        set_state('prometheus.do-reconfig-yml')
        set_state('prometheus.do-reconfig-def')
    if any((
        data_changed('prometheus.target_jobs', target_jobs),
        templates_changed([PROMETHEUS_YML_TMPL]),
    )):
        set_state('prometheus.do-reconfig-yml')
    if any((
        data_changed('prometheus.scrape_jobs', scrape_jobs),
        templates_changed([PROMETHEUS_YML_TMPL]),
    )):
        set_state('prometheus.do-reconfig-yml')
    if any((
        data_changed('prometheus.args', args),
        templates_changed([PROMETHEUS_DEF_TMPL]),
    )):
        set_state('prometheus.do-reconfig-def')
    remove_state('prometheus.do-check-reconfig')


@when('prometheus.do-restart')
def restart_prometheus():
    if not host.service_running(SVCNAME):
        hookenv.log('Starting {}...'.format(SVCNAME))
        host.service_start(SVCNAME)
    else:
        hookenv.log('Restarting {}, config file changed...'.format(SVCNAME))
        host.service_restart(SVCNAME)
    hookenv.status_set('active', 'Ready')
    set_state('prometheus.started')
    remove_state('prometheus.do-restart')


# Relations
@when('prometheus.started')
@when_not('target.available')
def update_prometheus_no_targets():
    unitdata.kv().set('target_jobs', [])
    data_changed('target.related_services', [])
    set_state('prometheus.do-check-reconfig')

    
@when('prometheus.started')
@when_not('scrape.available')
def update_prometheus_no_targets():
    unitdata.kv().set('scrape_jobs', [])
    set_state('prometheus.do-check-reconfig')

    
@when('prometheus.started')
@when('target.available')
def update_prometheus_targets(target):
    services = target.services()
    related_targets = []
    for service in services:
        targets = []
        for unit in service['hosts']:
            hookenv.log('{} has a unit {}:{}'.format(
                service['service_name'],
                unit['hostname'],
                unit['port']))
            targets.append('{hostname}:{port}'.format(**unit))
        related_targets.append({'job_name': service['service_name'],
                                'targets': targets})

    unitdata.kv().set('target_jobs', related_targets)
    set_state('prometheus.do-check-reconfig')


@when('prometheus.started')
@when('scrape.available')
def update_prometheus_scrape_targets(target):
    targets = target.targets()
    unitdata.kv().set('scrape_jobs', targets)
    set_state('prometheus.do-check-reconfig')
    

@when('prometheus.started')
@when_not('alertmanager-service.available')
def update_prometheus_no_alertmanager():
    runtime_args('-alertmanager.url', None)
    set_state('prometheus.do-check-reconfig')


@when('prometheus.started')
@when('alertmanager-service.available')
def update_prometheus_alertmanager(alertmanager):
    services = alertmanager.services()
    if not (data_changed('alertmanager-service.related_services', services)):
        return
    # XXX: as of prometheus 0.17, it can only point to a single alertmanager,
    #      last one from below loop will be used.
    for service in services:
        for unit in service['hosts']:
            hookenv.log('{} has a unit {}:{}'.format(
                service['service_name'],
                unit['hostname'],
                unit['port']))
            runtime_args('-alertmanager.url',
                         'http://{hostname}:{port}'.format(**unit))
    set_state('prometheus.do-check-reconfig')


@when('nrpe-external-master.available')
def update_nrpe_config(svc):
    # python-dbus is used by check_upstart_job
    fetch.apt_install('python-dbus')
    hostname = nrpe.get_nagios_hostname()
    current_unit = nrpe.get_nagios_unit_name()
    nrpe_setup = nrpe.NRPE(hostname=hostname)
    nrpe.add_init_service_checks(nrpe_setup, [SVCNAME], current_unit)
    nrpe_setup.write()


@when('prometheus.started')
@when('grafana-source.available')
def provide_grafana_source(grafana):
    kv = unitdata.kv()
    port = kv.get('prometheus.port')
    grafana.provide('prometheus', port, 'Juju generated source')
