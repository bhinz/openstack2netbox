#  MIT License
#
#  Copyright (c) 2025. Patrick Brammerloo, Mark Zijdemans, DirectVPS [https://directvps.nl/]
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#
#  The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#  SOFTWARE.

import sys
import re

from scripts.netbox.create import createnetboxvm
from scripts.netbox.update import updatenetboxvm
from scripts.openstack.checkstatus import getstatus

import settings
keystone = settings.keystone
nova = settings.nova
cinder = settings.cinder
nb = settings.nb
cluster_name = settings.cluster_name

unchangedvms = 0
image_name_cache = {}
image_os_type_cache = {}
image_os_distro_cache = {}
volume_metadata_cache = {}


def _extract_image_field(obj, keys):
    if obj is None:
        return None

    candidate_maps = []
    if isinstance(obj, dict):
        candidate_maps.append(obj)

    properties = getattr(obj, 'properties', None)
    if isinstance(properties, dict):
        candidate_maps.append(properties)

    metadata = getattr(obj, 'metadata', None)
    if isinstance(metadata, dict):
        candidate_maps.append(metadata)

    for map_obj in candidate_maps:
        for key in keys:
            value = map_obj.get(key)
            if value:
                return str(value).strip().lower()

    for key in keys:
        direct_value = getattr(obj, key, None)
        if direct_value:
            return str(direct_value).strip().lower()

    return None


def _get_boot_volume_ids(instance):
    attached_volumes = getattr(instance, 'os-extended-volumes:volumes_attached', None)
    if not attached_volumes:
        return []

    volume_ids = []
    for attached_volume in attached_volumes:
        volume_id = attached_volume.get('id')
        if volume_id:
            volume_ids.append(volume_id)
    return volume_ids


def _resolve_boot_volume_metadata(instance):
    volume_ids = _get_boot_volume_ids(instance)
    if not volume_ids:
        return None, None

    for volume_id in volume_ids:
        if volume_id in volume_metadata_cache:
            cached_image_name, cached_image_id, cached_os_type, cached_os_distro = volume_metadata_cache[volume_id]
            if cached_image_name != "Unknown" or cached_os_type is not None or cached_os_distro is not None:
                return cached_image_name, cached_image_id, cached_os_type, cached_os_distro
            continue

        volume_image_name = "Unknown"
        volume_image_id = None
        volume_os_type = None
        volume_os_distro = None
        try:
            volume_obj = cinder.volumes.get(volume_id)
            volume_metadata = getattr(volume_obj, 'volume_image_metadata', None)
            if isinstance(volume_metadata, dict):
                metadata_image_name = volume_metadata.get('image_name')
                if metadata_image_name:
                    volume_image_name = str(metadata_image_name)[:128]

                metadata_image_id = volume_metadata.get('image_id')
                if metadata_image_id:
                    volume_image_id = str(metadata_image_id)

                metadata_os_type = volume_metadata.get('os_type')
                if metadata_os_type:
                    volume_os_type = str(metadata_os_type).strip().lower()

                metadata_os_distro = volume_metadata.get('os_distro')
                if metadata_os_distro:
                    volume_os_distro = str(metadata_os_distro).strip().lower()
        except Exception:
            pass

        volume_metadata_cache[volume_id] = (volume_image_name, volume_image_id, volume_os_type, volume_os_distro)
        if volume_image_name != "Unknown" or volume_os_type is not None or volume_os_distro is not None:
            return volume_image_name, volume_image_id, volume_os_type, volume_os_distro

    return "Unknown", None, None, None


def _resolve_image_details(instance):
    image_payload = getattr(instance, 'image', None)
    if not image_payload:
        volume_image_name, volume_image_id, volume_os_type, volume_os_distro = _resolve_boot_volume_metadata(instance)
        return volume_image_name, volume_image_id, volume_os_type, volume_os_distro

    if isinstance(image_payload, dict):
        image_name = image_payload.get('name')
        image_id = image_payload.get('id')
    else:
        image_name = getattr(image_payload, 'name', None)
        image_id = getattr(image_payload, 'id', None)

    if image_name and image_id:
        image_name_cache[image_id] = str(image_name)[:128]

    if image_name and image_id in image_os_type_cache and image_id in image_os_distro_cache:
        return str(image_name)[:128], str(image_id), image_os_type_cache[image_id], image_os_distro_cache[image_id]

    if image_name and not image_id:
        volume_image_name, volume_image_id, volume_os_type, volume_os_distro = _resolve_boot_volume_metadata(instance)
        return str(image_name)[:128], volume_image_id, volume_os_type, volume_os_distro

    if not image_id:
        volume_image_name, volume_image_id, volume_os_type, volume_os_distro = _resolve_boot_volume_metadata(instance)
        return volume_image_name, volume_image_id, volume_os_type, volume_os_distro

    if image_id in image_name_cache and image_id in image_os_type_cache and image_id in image_os_distro_cache:
        return image_name_cache[image_id], str(image_id), image_os_type_cache[image_id], image_os_distro_cache[image_id]

    resolved_name = "Unknown"
    resolved_os_type = None
    resolved_os_distro = None
    try:
        if hasattr(nova, 'glance'):
            glance_image = nova.glance.find_image(image_id)
            resolved_name = str(glance_image.name)[:128]
            resolved_os_type = _extract_image_field(glance_image, ("os_type"))
            resolved_os_distro = _extract_image_field(glance_image, ("os_distro"))
    except Exception:
        pass

    if resolved_name == "Unknown" or resolved_os_type is None or resolved_os_distro is None:
        try:
            # Fallback for deployments exposing image retrieval via Nova.
            image_obj = nova.images.get(image_id)
            resolved_name = str(image_obj.name)[:128]
            resolved_os_type = _extract_image_field(image_obj, ("os_type"))
            resolved_os_distro = _extract_image_field(image_obj, ("os_distro"))
        except Exception:
            pass

    if resolved_name == "Unknown" or resolved_os_type is None or resolved_os_distro is None:
        volume_image_name, volume_image_id, volume_os_type, volume_os_distro = _resolve_boot_volume_metadata(instance)
        if resolved_name == "Unknown" and volume_image_name != "Unknown":
            resolved_name = volume_image_name
        if volume_image_id is not None:
            image_id = volume_image_id
        if resolved_os_type is None and volume_os_type is not None:
            resolved_os_type = volume_os_type
        if resolved_os_distro is None and volume_os_distro is not None:
            resolved_os_distro = volume_os_distro

    normalized_image_id = str(image_id)
    image_name_cache[normalized_image_id] = resolved_name
    image_os_type_cache[normalized_image_id] = resolved_os_type
    image_os_distro_cache[normalized_image_id] = resolved_os_distro
    return resolved_name, normalized_image_id, resolved_os_type, resolved_os_distro


def nova_to_netboxvms(myinstances, nova_dictionary, keystone_dictionary,  netbox_vm_dictionary):
    global unchangedvms
    for os_instance in myinstances:
        os_nova_vm = define_nova_object(os_instance, nova_dictionary, keystone_dictionary)
        try:
            # print(vars(os_nova_vm))
            if os_nova_vm.instance_id in netbox_vm_dictionary.keys() and os_nova_vm.custom_name in str(netbox_vm_dictionary.values()):
                # First we check for custom-named VMs we were forced to create, whenever there were duplicates
                # NetBox doesn't allow unique names per cluster, unless a Tenant was assigned to said VM
                netboxvm = netbox_vm_dictionary.get(os_nova_vm.instance_id)
                nb_vm = CreateNetboxVmObject(netboxvm)
                compare_vm_objects(os_nova_vm, nb_vm)
            elif os_nova_vm.instance_id in netbox_vm_dictionary.keys():
                netboxvm = netbox_vm_dictionary.get(os_nova_vm.instance_id)
                nb_vm = CreateNetboxVmObject(netboxvm)
                compare_vm_objects(os_nova_vm, nb_vm)
            elif (os_nova_vm.instance_id not in netbox_vm_dictionary.keys() and
                  os_nova_vm.name in str(netbox_vm_dictionary.values()) and
                    nb.virtualization.virtual_machines.get(name=os_nova_vm.name, cluster_name=cluster_name,
                                                              tag="openstack-api-script") is not None):
                # We're dealing with a new VM that may, or may not be, a replacement of an older VM
                # So we fetch the ID of said machine, based on the OpenStack name and then replace its values
                nbvm_fetch = nb.virtualization.virtual_machines.get(name=os_nova_vm.name, cluster_name=cluster_name,
                                                              tag="openstack-api-script")
                if nbvm_fetch.custom_fields["openstack_tenant"] == os_nova_vm.tenant:
                    # If there is a NB VM in the same NB cluster with the same OS VM-name + OS tenant,
                    # we will assume it is a replacement
                    # Notably, the passed instance.id will overwrite the 'old' OpenStack Instance ID field
                    # The next run, our second or first if statement should trigger for this specific Instance instead
                    nb_vm = CreateNetboxVmObject(nbvm_fetch)
                    compare_vm_objects(os_nova_vm, nb_vm)
                else:
                    # If the tenant is not equal, we create a new VM instead
                    createnetboxvm(os_nova_vm)
            else:
                # Finally we create the Netbox VM if we couldn't find or compare it to anything NetBox.
                createnetboxvm(os_nova_vm)
        except Exception as e:
            print(f"Unable to create or update VM {os_nova_vm.name} \n{e}")
            print(vars(os_nova_vm))
            sys.exit(1)
    print(f"Skipped {unchangedvms} VMS in total, because there were no changes.")


def define_nova_object(instance, flavordictionary, tenantdictionary):
    os_instance_flavorname = flavordictionary[instance.flavor['id']]['name']
    os_instance_flavorcpu = flavordictionary[instance.flavor['id']]['vcpu']
    os_instance_flavorram = flavordictionary[instance.flavor['id']]['ram']
    os_instance_flavorswap = flavordictionary[instance.flavor['id']]['swap']
    os_instance_flavordisk = flavordictionary[instance.flavor['id']]['disk']
    os_instance_flavorephemeral = flavordictionary[instance.flavor['id']]['ephemeral']
    image_name, image_id, os_type, os_distro = _resolve_image_details(instance)
    platform_id = settings.get_platform_id_by_image_id(image_id)
    platform_name = None
    custom_instance_name = instance.name[:53] + "_[" + instance.id[:8] + "]"
    instancename = instance.name[:64]
    try:
        instancetenant = tenantdictionary[instance.tenant_id]['name']  # We fetch Tenant name from our dictionary
    except Exception as e:
        if tenantdictionary == "none":
            # This is where we attempt fetching Keystone information for the last time
            # but only if collectopenstackinformation() didn't populate tenantdictionary properly
            try:
                instancetenant = keystone.projects.get(instance.tenant_id)  # We fetch Tenant name via Keystone call
                instancetenant = instancetenant.name
            except Exception as e:
                print(f"Unable to access OpenStack Keystone tenant name \n{e}")
                sys.exit(1)
        else:
            print(f"Unable to populate Keystone instancetenant variable for instance {instance} \n{e}")
            print("This Instance is likely associated with a Tenant that does not exist")
            instancetenant = "Not associated with a Tenant"
            # sys.exit(1)
    try:
        currentstatus = getstatus(instance.status)  # We transform OpenStack statuses to Netbox statuses
    except Exception as e:
        print(f"Unable to transform OpenStack status to Netbox status \n{e}")
        sys.exit(1)
    try:
        instancehypervisor = getattr(instance, 'OS-EXT-SRV-ATTR:host')  # Admin-only call/attribute
        if instancehypervisor is None:
            instancehypervisor = "Unknown"  # Shelved Instances cause instancehypervisor to be None
    except Exception as e:
        if str(e) == "OS-EXT-SRV-ATTR:host":  # If we can't get the hypervisor name, NB field will become "Unknown"
            instancehypervisor = "Unknown"
        else:
            print(f"Unable to fetch and or set instancehypervisor variable \n{e}")
            sys.exit(1)
    try:
        hostname = getattr(instance, 'OS-EXT-SRV-ATTR:hostname', instance.name)

        if not hostname:
            hostname = "unknown"

    except Exception as e:
        print(f"Fehler beim Abrufen des Namens für {instance.name}: {e}")
        hostname = "unknown"

    nova_vm = CreateNovaVmObject(instancename, custom_instance_name, instance.id, instancetenant,
                                 currentstatus, instancehypervisor, hostname,
                                 os_instance_flavorname, os_instance_flavorcpu, os_instance_flavorram,
                                 os_instance_flavorswap, os_instance_flavordisk, os_instance_flavorephemeral,
                                 image_name, image_id, os_type, os_distro, platform_name, platform_id)
    return nova_vm


class CreateNovaVmObject(object):
    def __init__(self, name, customname, instance_id, tenant, status, hypervisor, hostname,
                 flavorname, flavorcpu, flavorram, flavorswap, flavordisk, flavorephemeral,
                 image_name, image_id, os_type, os_distro, platform_name, platform_id):
        self.name = name
        self.custom_name = customname
        self.instance_id = instance_id
        self.tenant = tenant
        self.status = status
        self.hypervisor = hypervisor
        self.hostname = hostname
        self.flavorname = flavorname
        self.flavorcpu = int(flavorcpu)
        self.flavorram = int(flavorram)
        self.flavorswap = int(flavorswap)
        self.flavordisk = int(flavordisk)
        self.flavorephemeral = int(flavorephemeral)
        self.image_name = image_name
        self.image_id = image_id
        self.os_type = os_type
        self.os_distro = os_distro
        self.platform_name = platform_name
        self.platform_id = platform_id


class CreateNetboxVmObject(object):
    def __init__(self, dictionary):
        self.name = dictionary.name
        self.netbox_id = dictionary.id
        self.openstack_id = dictionary.custom_fields["openstack_id"]
        self.tenant = dictionary.custom_fields["openstack_tenant"]
        status = str(dictionary.status)
        status = status.lower()
        self.status = status
        self.hypervisor = dictionary.custom_fields["openstack_hypervisor"]
        self.hostname = dictionary.custom_fields["openstack_hostname"]
        self.flavorname = dictionary.custom_fields["openstack_flavor"]
        cpu = dictionary.vcpus
        self.flavorcpu = int(cpu)
        self.flavorram = dictionary.memory
        self.flavorswap = dictionary.custom_fields["openstack_swap"]
        self.flavordisk = dictionary.disk
        self.flavorephemeral = dictionary.custom_fields["openstack_ephemeral"]
        if settings.netbox_has_openstack_image_cf:
            self.image_name = dictionary.custom_fields.get("openstack_image")
        else:
            self.image_name = None

        if dictionary.platform is not None:
            self.platform_id = dictionary.platform.id
            self.platform_name = dictionary.platform.name
        else:
            self.platform_id = None
            self.platform_name = None


def compare_vm_objects(os_nova_vm_obj, nb_vm_obj):
    global unchangedvms
    if os_nova_vm_obj.hostname != "unknown":
        pass
    elif nb_vm_obj.hostname != "unknown" and os_nova_vm_obj.hostname == "unknown":
        # After a certain amount of time, the hostname may become unavailable in the OpenStack console
        # If the NetBox side has a hostname set, we ignore the OpenStack value if it is our default of unknown
        os_nova_vm_obj.hostname = nb_vm_obj.hostname
    try:
        image_name_changed = settings.netbox_has_openstack_image_cf and nb_vm_obj.image_name != os_nova_vm_obj.image_name
        platform_changed = os_nova_vm_obj.platform_id is not None and nb_vm_obj.platform_id != os_nova_vm_obj.platform_id

        if ((nb_vm_obj.name != os_nova_vm_obj.name and nb_vm_obj.name != os_nova_vm_obj.custom_name) or
                nb_vm_obj.openstack_id != os_nova_vm_obj.instance_id or
                nb_vm_obj.tenant != os_nova_vm_obj.tenant or
                nb_vm_obj.status != os_nova_vm_obj.status or
                nb_vm_obj.hypervisor != os_nova_vm_obj.hypervisor or
                nb_vm_obj.hostname != os_nova_vm_obj.hostname or
                nb_vm_obj.flavorname != os_nova_vm_obj.flavorname or
                nb_vm_obj.flavorcpu != os_nova_vm_obj.flavorcpu or
                nb_vm_obj.flavorram != os_nova_vm_obj.flavorram or
                nb_vm_obj.flavorswap != os_nova_vm_obj.flavorswap or
                image_name_changed or
                platform_changed or
                # We skip disk as it is defined by Virtual Disks
                nb_vm_obj.flavorephemeral != os_nova_vm_obj.flavorephemeral):
            #print(vars(os_nova_vm_obj))
            #print(vars(nb_vm_obj))
            updatenetboxvm(nb_vm_obj.netbox_id, os_nova_vm_obj, nb_vm_obj.platform_id)
        else:
            unchangedvms = unchangedvms + 1
            if (unchangedvms % 10) == 0:
                print(f"Skipped {unchangedvms} VMs because nothing changed")
            else:
                pass
            pass
    except Exception as e:
        print(f"Unable to compare OpenStack Instance to Netbox Virtual Machine:\n")
        print(f"{e}\n")
        print(f"{vars(os_nova_vm_obj)}\n")
        print(f"{vars(nb_vm_obj)}\n")
        sys.exit(1)
