#  MIT License
#
#  Copyright (c) 2026. OpenStack2NetBox contributors
#
#  Permission is hereby granted, free of charge, to any person obtaining a copy
#  of this software and associated documentation files (the "Software"), to deal
#  in the Software without restriction, including without limitation the rights
#  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the Software is
#  furnished to do so, subject to the following conditions:
#
#  The above copyright notice and this permission notice shall be included in all
#  copies or substantial portions of the Software.
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
#  SOFTWARE.

import re
import sys

import settings
nb = settings.nb


def _platform_slug_from_image_id(image_id):
    return f"openstack-image-{str(image_id).lower()}"


def _extract_os_version(image_name):
    if image_name is None:
        return None

    version_candidates = re.findall(r'\b\d+(?:[._-]\d+){0,2}\b', image_name)
    for candidate in version_candidates:
        normalized_candidate = candidate.replace('_', '.').replace('-', '.')
        parts = normalized_candidate.split('.')

        # Skip obvious dd.mm.yyyy style dates.
        if (len(parts) == 3 and len(parts[0]) <= 2 and len(parts[1]) <= 2 and len(parts[2]) == 4):
            continue

        return normalized_candidate

    return None


def _build_platform_custom_fields(os_image, os_version):
    custom_fields = {}
    os_type_value = os_image.get('os_type')
    os_distro_value = os_image.get('os_distro')

    if settings.netbox_has_platform_os_type_cf and os_type_value is not None:
        custom_fields['openstack_type'] = os_type_value

    if settings.netbox_has_platform_os_distro_cf and os_distro_value is not None:
        custom_fields['openstack_distro'] = os_distro_value

    if settings.netbox_has_platform_openstack_image_id_cf:
        custom_fields['openstack_image_id'] = os_image['image_id']

    if settings.netbox_has_platform_os_version_cf and os_version is not None:
        custom_fields['openstack_version'] = os_version
    return custom_fields


def _build_platform_create_payload(os_image):
    image_name = os_image['image_name']
    image_id = os_image['image_id']
    os_version = _extract_os_version(image_name)
    platform_slug = _platform_slug_from_image_id(image_id)

    payload = {
        'name': image_name,
        'slug': platform_slug,
    }

    custom_fields = _build_platform_custom_fields(os_image, os_version)
    if custom_fields:
        payload['custom_fields'] = custom_fields
    elif os_version is not None:
        payload['description'] = f"OpenStack image version: {os_version}"

    return payload


def _build_platform_update_payload(existing_platform, os_image):
    os_version = _extract_os_version(os_image['image_name'])
    payload = {'id': existing_platform.id}

    if existing_platform.name != os_image['image_name']:
        payload['name'] = os_image['image_name']

    custom_fields = _build_platform_custom_fields(os_image, os_version)
    if custom_fields:
        payload['custom_fields'] = custom_fields
    elif os_version is not None:
        payload['description'] = f"OpenStack image version: {os_version}"

    return payload


def glanceimages_to_netboxplatforms(openstack_image_dictionary):
    if not settings.netbox_has_platform_openstack_image_id_cf:
        print("NetBox custom field openstack_image_id for dcim.platform is required for image sync.")
        sys.exit(1)

    created_platforms = 0
    updated_platforms = 0
    skipped_platforms = 0

    for image_id in openstack_image_dictionary:
        os_image = openstack_image_dictionary[image_id]
        image_name = os_image['image_name']

        if image_name is None or image_name == "":
            skipped_platforms += 1
            continue

        try:
            platform_matches = list(nb.dcim.platforms.filter(cf_openstack_image_id=os_image['image_id']))
        except Exception as e:
            print(f"Unable to query NetBox platform for image {image_name} \n{e}")
            sys.exit(1)

        if len(platform_matches) > 1:
            print(f"Found multiple NetBox platforms for OpenStack image ID {os_image['image_id']}. Skipping this image.")
            skipped_platforms += 1
            continue

        existing_platform = platform_matches[0] if len(platform_matches) == 1 else None

        if existing_platform is None:
            create_payload = _build_platform_create_payload(os_image)
            try:
                nb.dcim.platforms.create(**create_payload)
                created_platforms += 1
            except Exception as e:
                print(f"Unable to create NetBox platform for image {image_name} \n{e}")
                sys.exit(1)
            continue

        update_payload = _build_platform_update_payload(existing_platform, os_image)
        if len(update_payload.keys()) == 1:
            skipped_platforms += 1
            continue

        try:
            nb.dcim.platforms.update([update_payload])
            updated_platforms += 1
        except Exception as e:
            print(f"Unable to update NetBox platform for image {image_name} \n{e}")
            sys.exit(1)

    print(f"Created {created_platforms} NetBox platforms based on OpenStack images")
    print(f"Updated {updated_platforms} NetBox platforms based on OpenStack images")
    print(f"Skipped {skipped_platforms} OpenStack images for NetBox platforms")
