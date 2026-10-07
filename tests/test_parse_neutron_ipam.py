import contextlib
import importlib.util
import io
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class NetboxIpamTests(unittest.TestCase):
    def setUp(self):
        settings = ModuleType('settings')
        settings.nb = Mock()
        settings.cluster_name = 'test-cluster'
        create = ModuleType('scripts.netbox.create')
        create.createglobalipamip = Mock()
        create.createlanipamip = Mock()
        update = ModuleType('scripts.netbox.update')
        update.updateglobalipamip = Mock()
        update.updatelanipamip = Mock()
        module_path = Path(__file__).resolve().parents[1] / 'scripts' / 'parse_neutron_ipam.py'
        spec = importlib.util.spec_from_file_location('ipam_under_test', module_path)
        self.ipam = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {
            'settings': settings,
            'scripts.netbox.create': create,
            'scripts.netbox.update': update,
        }):
            spec.loader.exec_module(self.ipam)
        self.ipam.netboxipamlanip = Mock()
        self.ipam.netboxipamglobalip = Mock()
        self.vm = SimpleNamespace(id=1, name='vm')
        self.interface = SimpleNamespace(id=2, name='eth0')
        self.vrf = SimpleNamespace(id=3)
        self.port = {
            'interfaceid': 'port-id',
            'interfaceassociation': 'vm-id',
            'interfacestatus': 'ACTIVE',
            'osifdeviceowner': 'compute:nova',
            'interfacenetwork': 'network-id',
            'interfaceips': [{'ip_address': '10.0.0.1', 'subnet_id': 'subnet-id'}],
        }
        self.floating = {
            'boundtoinstanceid': 'vm-id',
            'boundtointerfaceid': 'port-id',
            'boundtonetworkid': 'network-id',
            'floatip': '8.8.8.8',
        }

    def test_fixed_ip_missing_vm_skips_and_continues(self):
        missing_vm_port = dict(self.port, interfaceassociation='missing-vm')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.ipam.netboxipam(
                {'stale-port': missing_vm_port, 'valid-port': self.port},
                {'subnet-id': {'subnet_prefix': 24}},
                {'vm-id': self.vm}, {'port-id': self.interface},
                {'network-id': self.vrf}, {}, {},
            )
        self.assertIn('stale-port', output.getvalue())
        self.assertIn('missing-vm', output.getvalue())
        self.ipam.netboxipamlanip.assert_called_once()
        address = self.ipam.netboxipamlanip.call_args.args[1]
        self.assertEqual(address.address, '10.0.0.1/24')
        self.assertEqual(address.nb_vm_id, self.vm.id)
        self.assertEqual(address.nb_int_id, self.interface.id)

    def test_floating_ip_missing_association_skips_and_continues(self):
        for missing_field in ('boundtoinstanceid', 'boundtointerfaceid'):
            with self.subTest(missing_field=missing_field):
                self.ipam.netboxipamglobalip.reset_mock()
                missing_association = dict(self.floating, **{missing_field: 'missing-id'})
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.ipam.netboxipamfloat(
                        {'stale-float': missing_association, 'valid-float': self.floating},
                        {}, {'vm-id': self.vm}, {'port-id': self.interface},
                        {'network-id': self.vrf}, {}, {},
                    )
                self.assertIn('stale-float', output.getvalue())
                self.assertIn('missing-id', output.getvalue())
                self.ipam.netboxipamglobalip.assert_called_once()
                address = self.ipam.netboxipamglobalip.call_args.args[1]
                self.assertEqual(address.address, '8.8.8.8')
                self.assertEqual(address.nb_vm_id, self.vm.id)
                self.assertEqual(address.nb_int_id, self.interface.id)


if __name__ == '__main__':
    unittest.main()