import base64

import yaml
from charmhelpers.contrib.charmsupport import nrpe
from charmhelpers.core import hookenv
from charmhelpers.core import host
from charmhelpers.core import unitdata
from charmhelpers.core.templating import render
from charmhelpers.fetch.snap import snap_install
from charms.reactive import hook
from charms.reactive import remove_state
from charms.reactive import set_state
from charms.reactive import when
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


@when_not('openstackexporter.installed')
def install_packages():
    hookenv.status_set('maintenance', 'Installing software')
    config = hookenv.config()
    channel = config.get('snap_channel', 'stable')
    # required for offline installs
    # uses charm store if no "core" resource is provided
    snap_install('core')
    snap_install(SNAP_NAME, '--{}'.format(channel))
    hookenv.status_set('maintenance', 'Software installed')
    set_state('openstackexporter.installed')


@hook('config-changed')
def check_reconfig_exporter():
    config = hookenv.config()
    if data_changed('openstackexporter.config', config):
        render_config()


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
    disable_metrics = config['disable_metrics'].strip().split(',')
    ctx = config.copy()
    services = [
        'image', 'compute', 'network', 'volume', 'identity', 'object-store',
        'load-balancer', 'container-infra', 'dns', 'baremetal', 'gnocchi']
    ctx['disabled'] = [item for item in services if not config[item]]
    ctx['disable_metrics'] = [
        item.strip() for item in disable_metrics if item.strip()
    ]
    ctx['credentials'] = CONFIG_MAP[CLOUDS]['target']
    ctx['config_file'] = CONFIG_MAP[DEFAULTS]['target']
    ctx['binary_file'] = BINARY_FILE
    if creds.get('ssl_ca'):
        hookenv.log('render_config: Using provided ssl_ca')
        render_path(CACERT, creds)
    render_path(CLOUDS, creds)
    render_path(DEFAULTS, ctx)
    render_path(SERVICE, ctx)
    restart_service()
    hookenv.status_set('active', 'Ready')


@when('openstackexporter.installed'
      'openstackexporter.authorized')
@when_not('openstackexporter.started')
def start():
    render_config()
    set_state('openstackexporter.started')


def restart_service():
    if not host.service_running(SVC_NAME):
        hookenv.log('Starting {}...'.format(SVC_NAME))
        host.service_start(SVC_NAME)
    else:
        hookenv.log('Restarting {}, config file changed...'.format(SVC_NAME))
        host.service_restart(SVC_NAME)
    host.service('enable', SVC_NAME)


@when('nrpe-external-master.available')
def update_nrpe_config():
    hostname = nrpe.get_nagios_hostname()
    nrpe_setup = nrpe.NRPE(hostname=hostname)
    config = hookenv.config()
    # Note(aluria): check_http addresses LP#1829470
    # (/metrics takes too long, while / check takes ms
    # A final fix will land once LP#1829496 is fixed
    # (we can't look now for "-s 'OpenStack Exporter'",
    # which is more explicit than "-s Exporter" body content)
    nrpe_setup.add_check(
        'openstack_exporter_http',
        'Openstack Exporter HTTP check',
        "check_http -I 127.0.0.1 -p {} -u / -s Exporter {}".format(
            config.get('port'), config.get('extra-nrpe-args')
        ))
    nrpe_setup.write()


@when('target.available')
def configure_http(target):
    try:
        config = hookenv.config()
        hookenv.log("Openstack-exporter endpoint available on port: {}".format(
            hookenv.config('port')
        ))
        hookenv.open_port(config.get('port'))
        target.configure(port=config.get('port'))
    except Exception as e:
        hookenv.log("Openstack-exporter endpoint failed: {}".format(str(e)),
                    level=hookenv.ERROR)


@when('config.changed.port',
      'endpoint.scrape.available')
def port_changed_scrape():
    prometheus = endpoint_from_name('scrape')
    config = hookenv.config()
    hookenv.log("Port changed to port: {}".format(
        config.get('port')
    ))
    hookenv.open_port(config.get('port'))
    prometheus.configure(port=config.get('port'))


@when('openstackexporter.started',
      'endpoint.scrape.available')
@when_not('golang-openstack-exporter.configured_port')
def set_provides_data():
    prometheus = endpoint_from_flag('endpoint.scrape.available')
    hookenv.log("Scrape endpoint available on port: {}".format(
        hookenv.config('port')
    ))
    config = hookenv.config()
    hookenv.open_port(config.get('port'))
    prometheus.configure(port=config.get('port'))
    set_state('golang-openstack-exporter.configured_port')


@when('golang-openstack-exporter.configured_port')
@when_not('endpoint.scrape.available')
def prometheus_left():
    hookenv.log("Scrape endpoint became unavailable")
    remove_state('golang-openstack-exporter.configured_port')


@when('identity.connected')
@when_not('openstackexporter.identityset')
def configure_keystone_username():
    try:
        keystone = endpoint_from_flag('identity.connected')
        keystone.request_credentials(SNAP_NAME)
        set_state('openstackexporter.identityset')
    except Exception as e:
        hookenv.log("Keystone endpoint setup failed: {}".format(str(e)),
                    level=hookenv.ERROR)


@when_not('identity.connected')
@when('openstackexporter.identityset')
def departed_keystone():
    remove_state('openstackexporter.identityset')
    hookenv.log("Keystone endpoint departed")


@when('identity.available.auth',
      'openstackexporter.identityset')
@when_not('openstackexporter.authorized')
def save_creds():
    try:
        keystone = endpoint_from_name('identity')
        data = {
            key: getattr(keystone, key.replace('-', '_'))()
            for key in keystone.auto_accessors
        }
        if data.get('credentials_username'):
            reconfig_on_change('keystone-relation-creds', data)
            set_state('openstackexporter.authorized')
    except Exception as e:
        hookenv.log("Keystone credentials failed: {}".format(str(e)),
                    level=hookenv.ERROR)


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
    render_config()
