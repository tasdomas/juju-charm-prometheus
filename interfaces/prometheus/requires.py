from charms.reactive import hook
from charms.reactive import RelationBase
from charms.reactive import scopes


class PrometheusRequires(RelationBase):
    scope = scopes.UNIT
    
    @hook('{requires:prometheus}-relation-{joined,changed}')
    def changed(self):
        conv = self.conversation()
        if conv.get_remote('port'):
            # this unit's conversation has a port, so
            # it is part of the set of available units
            conv.set_state('{relation_name}.available')
            
    @hook('{requires:prometheus}-relation-{departed,broken}')
    def broken(self):
        conv = self.conversation()
        conv.remove_state('{relation_name}.available')
            
    def targets(self):
        """
        Returns a list of available prometheus targets.
            [
                {
                    'job_name': name_of_job,
                    'metrics_path': path_to_metrics_endpoint(optional),
                    'scrape_interval': scrape_interval(optional),
                    'scrape_timeout': scrape_timeout(optional),
                    'hosts': [
                        {
                            'hostname': address_of_host,
                            'port': port_for_host,
                        },
                        # ...
                    ],
                },
                # ...
            ]
        """
        services = {}
        for conv in self.conversations():
            service_name = conv.scope.split('/')[0]
            service = services.setdefault(service_name, {
                'job_name': service_name,
                'targets': [],
            })
            host = conv.get_remote('hostname') or conv.get_remote('private-address')
            port = conv.get_remote('port')
            if host and port:
                service['targets'].append('%s:%s'.format((host, port)))
            if conv.get_remote('metrics_path'):
                service['metrics_path'] = conv.get_remote('metrics_path')
            if conv.get_remote('scrape_interval'):
                service['scrape_interval'] = conv.get_remote('scrape_interval')
            if conv.get_remote('scrape_timeout'):
                service['scrape_timeout'] = conv.get_remote('scrape_timeout')
        return [s for s in services.values() if s['hosts']]
