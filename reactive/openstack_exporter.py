import base64

import yaml
from charmhelpers.contrib.charmsupport import nrpe
from charmhelpers.core import hookenv
from charmhelpers.core import host
from charmhelpers.core import unitdata
from charmhelpers.core.templating import render
from charms.layer import snap
from charms.reactive import remove_state
from charms.reactive import set_state
from charms.reactive import when
from charms.reactive import when_any
from charms.reactive import when_not
from charms.reactive.helpers import data_changed
from charms.reactive.relations import endpoint_from_flag
from charms.reactive.relations import endpoint_from_name

BINARY_FILE = '/snap/golang-openstack-exporter/current/bin/openstack-exporter'
SNAP_NAME = 'golang-openstack-exporter'
SVC_NAME = 'golang-openstack-exporter.service'
VAR_SNAP_ETC = '/var/snap/golang-openstack-exporter/current'
# VAR_SNAP_COMMON = '/var/snap/golang-openstack-exporter/common'
VAR_SNAP_CA = '{etcdir}/ca.crt'.format(etcdir=VAR_SNAP_ETC)

DEFAULTS, SERVICE, CLOUDS, CACERT = ('defaults', 'service', 'clouds', 'cacert')
CONFIG_MAP = {
    'defaults': {
        'source': 'golang-openstack-exporter',
        'target': '/etc/default/golang-openstack-exporter',
    },
    'service': {
        'source': 'golang-openstack-exporter.service',
        'target': '/etc/systemd/system/golang-openstack-exporter.service',
    },
    'clouds': {
        'source': 'clouds.tpl',
        'target': '{etcdir}/clouds.yaml'.format(etcdir=VAR_SNAP_ETC),
    },
    'cacert': {
        'source': 'ca.crt',
        'target': VAR_SNAP_CA,
    }
}


@when_not('exporter.installed')
def install_packages():
    hookenv.status_set('maintenance', 'Installing software')
    config = hookenv.config()
    channel = config.get('snap_channel', 'stable')
    # required for offline installs
    # uses charm store if no "core" resource is provided
    snap.install('core')
    snap.install(SNAP_NAME, channel=channel, force_dangerous=False)
    hookenv.status_set('maintenance', 'Software installed')
    set_state('exporter.installed')


@when('identity-credentials.connected')
def configure_keystone_username(keystone):
    keystone.request_credentials(SNAP_NAME)


@when('identity-credentials.available')
def save_creds(keystone):
    reconfig_on_change('keystone-relation-creds', {
        key: getattr(keystone, key.replace('-', '_'))()
        for key in keystone.auto_accessors
    })


@when_any('exporter.installed', 'exporter.do-check-reconfig')
def check_reconfig_exporter():
    config = hookenv.config()
    if data_changed('exporter.config', config):
        set_state('exporter.do-reconfig')
    remove_state('exporter.do-check-reconfig')


def convert_from_base64(v):
    if not v:
        return v
    if v.startswith('-----BEGIN'):
        return v
    try:
        return base64.b64decode(v).decode()
    except TypeError:
        return v


# allow user to override credentials (and the need to be related to keystone)
# with 'os-credentials' YAML dict
def get_credentials():
    config = hookenv.config()
    config_creds_yaml = config.get('os-credentials')
    creds = {}
    if config_creds_yaml:
        config_creds = yaml.safe_load(config_creds_yaml)
        creds = {
            'credentials_username': config_creds['username'],
            'credentials_password': config_creds['password'],
            'credentials_project': config_creds.get('tenant_name', 'admin'),
            'region': config_creds['region_name'],
            'auth_url': config_creds['auth_url'],
        }
        identity_api_version = config_creds.get('identity_api_version', 2)
        if identity_api_version == 3:
            creds['credentials_identity_api_version'] = identity_api_version
            creds['credentials_user_domain_name'] = config_creds.get(
                'user_domain_name')
            creds['credentials_project_name'] = config_creds.get(
                'project_name', 'admin')
            creds['credentials_project_domain_name'] = config_creds.get(
                'project_domain_name', 'admin_domain')
            creds['credentials_project_id'] = config_creds.get(
                'project_id')
            creds['credentials_project_domain_id'] = config_creds.get(
                'project_domain_id')
    else:
        creds = unitdata.kv().get('keystone-relation-creds')
        # lp#1785864 - Workaround Keystone relation not yet ready.
        if creds is None:
            hookenv.log('get_credentials: config os-credentials not set and '
                        'identity-credentials relation not yet ready')
            return None
        for key, v1, v2 in [
            ('credentials_user_domain_name', 'credentials_user_domain_id', 'default'),  # noqa
            ('credentials_project_domain_name', 'credentials_project_domain_id', 'Default'),  # noqa
            ('api_version', 'identity_api_version', 3)
        ]:
            if not creds.get(key):
                creds[key] = creds.get(v1) if creds.get(v1) else v2
    ssl_ca = convert_from_base64(config.get('ssl_ca'))
    if ssl_ca:
        creds['ssl_ca'] = ssl_ca
        creds['cacert'] = VAR_SNAP_CA
    return creds


@when('exporter.do-reconfig')
def render_config():
    """Render the configuration for charm when all the interfaces are
    available.
    """
    hookenv.status_set('maintenance', 'Updating configs')
    # kv = unitdata.kv()
    creds = get_credentials()
    if not creds:
        hookenv.log('render_config: No credentials yet, skipping')
        hookenv.status_set('blocked', 'Waiting for credentials.  Please '
                           'set os-credentials or add keystone relation')
        return
    hookenv.log('render_config: Got credentials for username={}'.format(
        creds['credentials_username']
    ))
    config = hookenv.config()
    ctx = config
    services = ['image', 'compute', 'network', 'volume', 'identity']
    ctx['disabled'] = [item for item in services if not config[item]]
    ctx['credentials'] = CONFIG_MAP[CLOUDS]['target']
    ctx['config_file'] = CONFIG_MAP[DEFAULTS]['target']
    ctx['binary_file'] = BINARY_FILE
    if creds.get('ssl_ca'):
        hookenv.log('render_config: Using provided ssl_ca')
        render_path(CACERT, creds)
    render_path(CLOUDS, creds)
    render_path(DEFAULTS, ctx)
    render_path(SERVICE, ctx)
    remove_state('exporter.do-reconfig')
    set_state('exporter.do-restart')


def restart_service():
    if not host.service_running(SVC_NAME):
        hookenv.log('Starting {}...'.format(SVC_NAME))
        host.service_start(SVC_NAME)
    else:
        hookenv.log('Restarting {}, config file changed...'.format(SVC_NAME))
        host.service_restart(SVC_NAME)
    host.service('enable', SVC_NAME)


@when('exporter.do-restart')
def do_restart():
    restart_service()
    hookenv.status_set('active', 'Ready')
    remove_state('exporter.do-restart')
    set_state('golang-openstack-exporter.started')


@when('golang-openstack-exporter-service.available'
      'golang-openstack-exporter.started')
def configure_exporter_service(exporter_service):
    config = hookenv.config()
    exporter_service.configure(config.get('port'))


@when('nrpe-external-master.available')
def update_nrpe_config(svc):
    hostname = nrpe.get_nagios_hostname()
    nrpe_setup = nrpe.NRPE(hostname=hostname)
    config = hookenv.config()
    port = config.get('port')
    extra_nrpe_args = config.get('extra-nrpe-args')
    # Note(aluria): check_http addresses LP#1829470
    # (/metrics takes too long, while / check takes ms
    # A final fix will land once LP#1829496 is fixed
    # (we can't look now for "-s 'OpenStack Exporter'",
    # which is more explicit than "-s Exporter" body content)
    nrpe_setup.add_check(
        'openstack_exporter_http',
        'Openstack Exporter HTTP check',
        "check_http -I 127.0.0.1 -p {} -u / -s Exporter {}".format(
            port, extra_nrpe_args
        ))
    nrpe_setup.write()


@when('config.changed.port',
      'endpoint.scrape.available',
      'golang-openstack-exporter.configured_port')
def port_changed():
    prometheus = endpoint_from_name('scrape')
    hookenv.log("Port changed, telling relations. ({})".format(
        hookenv.config('port')
    ))
    hookenv.open_port(hookenv.config('port'))
    prometheus.configure(port=hookenv.config('port'))


@when('golang-openstack-exporter.started',
      'endpoint.scrape.available')
@when_not('golang-openstack-exporter.configured_port')
def set_provides_data():
    prometheus = endpoint_from_flag('endpoint.scrape.available')
    hookenv.log("Scrape Endpoint became available. Telling port. ({})".format(
        hookenv.config('port')
    ))
    hookenv.open_port(hookenv.config('port'))
    prometheus.configure(port=hookenv.config('port'))
    set_state('golang-openstack-exporter.configured_port')


@when_not('endpoint.scrape.available')
@when('golang-openstack-exporter.configured_port')
def prometheus_left():
    hookenv.log("Scrape Endpoint became unavailable")
    remove_state('golang-openstack-exporter.configured_port')


def render_path(key, context):
    render(
        source=CONFIG_MAP[key]['source'],
        target=CONFIG_MAP[key]['target'],
        context=context
    )


def reconfig_on_change(key, data):
    if not data_changed(key, data):
        hookenv.log(
            '{} data unchanged, skipping reconfig'.format(key),
            level=hookenv.DEBUG
        )
        return
    unitdata.kv().set(key, data)
    hookenv.log(
        '{} data changed, triggering reconfig'.format(key),
        level=hookenv.DEBUG
    )
    set_state('exporter.do-reconfig')
