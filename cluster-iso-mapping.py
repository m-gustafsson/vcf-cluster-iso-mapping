#!/usr/bin/env python3

"""
VMware Cloud Foundation — Custom ISO → Cluster Mapping

Author: Martin Gustafsson
Email: martin.gustafsson(at)broadcom.com
Site/Blog: https://www.martingustafsson.com

Summary:
- Maps clusters to custom ESXi ISOs (per-vendor or per-cluster)
- Handles mixed-vendor clusters (skips non-selected vendor hosts)
- Writes JSON spec and updates LCM properties
"""

import json
import subprocess
import sys
import requests
from getpass import getpass
import logging
import urllib3
import os
import re
import argparse
import time

# Disable warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Logging setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')
from logging.handlers import RotatingFileHandler
log_path = os.path.abspath('skip_hosts.log')
file_handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Constants
CUSTOM_ISO_SPEC_FILENAME = 'generated_custom_iso_spec.json'
DEFAULT_CUSTOM_ISO_SPEC_PATH = '/opt/vmware/vcf/lcm/'
LCM_PROPERTIES_FILE = '/opt/vmware/vcf/lcm/lcm-app/conf/application-prod.properties'
LOCAL_PATH = 'https://localhost'
SKIP_HOST_PROPERTY = 'esx.upgrade.skip.host.ids='
CUSTOM_IMAGE_PROPERTY = 'lcm.esx.upgrade.custom.image.spec='

# Globals
all_hosts_map = {}
cluster_hosts_map = {}
cluster_id_name = {}
domain_hosts_map = {}
domain_name_cluster_id_map = {}
domain_name_id = {}
global_vendor_iso_map = {}
esx_custom_image_spec_list = []
hosts_to_skip = []
one_custom_iso_per_vendor = False
global_bundle_id = None
global_target_esx_version = None

# Data classes
class EsxCustomImageSpecObj:
    def __init__(self, bundle_id, target_esxi_version, domain_id, custom_iso_absolute_path, cluster_id=None):
        self.bundleId = bundle_id
        self.targetEsxVersion = target_esxi_version
        self.useVcfBundle = False
        self.domainId = domain_id
        if cluster_id:
            self.clusterId = cluster_id
        self.customIsoAbsolutePath = custom_iso_absolute_path

class Host:
    def __init__(self, id, fqdn, domain_id, cluster_id, vendor):
        self.id = id
        self.fqdn = fqdn
        self.domain_id = domain_id
        self.cluster_id = cluster_id
        self.vendor = vendor

# Utility functions
def check_if_directory_exists(dirname):
    return os.path.isdir(dirname)

def check_if_iso_exists(iso):
    return os.path.isfile(iso) and iso.lower().endswith('.iso')

def check_if_valid_sso(user, pwd):
    try:
        data = json.dumps({'username': user, 'password': pwd})
        resp = requests.post(f"{LOCAL_PATH}/v1/tokens",
                             headers={'Content-Type':'application/json'},
                             data=data, verify=False)
        return resp.status_code == 200
    except Exception:
        return False

def get_auth_headers(user, pwd):
    data = json.dumps({'username': user, 'password': pwd})
    resp = requests.post(f"{LOCAL_PATH}/v1/tokens",
                         headers={'Content-Type':'application/json'},
                         data=data, verify=False)
    token = resp.json().get('accessToken')
    return {'Content-Type':'application/json', 'Authorization':f'Bearer {token}'}

def execute_cmd_locally(cmd, log_stdout=True):
    logger.debug(f"RUN: {cmd}")
    ps = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = ps.communicate()
    out_s = out.decode() if out else ""
    err_s = err.decode() if err else ""
    if log_stdout and out_s.strip():
        logger.debug(out_s.strip())
    if err_s.strip():
        logger.error(err_s.strip())
    logger.debug(f"EXIT {ps.returncode}")
    return ps.returncode, out_s, err_s

# Inventory fetch
def get_all_domain_names_in_env(headers):
    resp = requests.get(f"{LOCAL_PATH}/v1/domains", headers=headers, verify=False)
    for d in resp.json().get('elements', []):
        name = d['name']
        did = d['id']
        domain_name_id[name] = did
        domain_name_cluster_id_map[name] = [c['id'] for c in d.get('clusters', [])]


def get_all_clusters(headers):
    resp = requests.get(f"{LOCAL_PATH}/v1/clusters", headers=headers, verify=False)
    for c in resp.json().get('elements', []):
        cluster_id_name[c['id']] = c['name']


def get_all_hosts(headers):
    resp = requests.get(f"{LOCAL_PATH}/v1/hosts", headers=headers, verify=False)
    for h in resp.json().get('elements', []):
        if h.get('status') == 'ASSIGNED':
            host = Host(h['id'], h['fqdn'], h['domain']['id'], h['cluster']['id'], h['hardwareVendor'])
            all_hosts_map[host.id] = host
            cluster_hosts_map.setdefault(host.cluster_id, []).append(host.id)
            domain_hosts_map.setdefault(host.domain_id, []).append(host.id)

def get_esx_bundle_upgrade_to_version(bundle_id, headers):
    resp = requests.get(f"{LOCAL_PATH}/v1/bundles/{bundle_id}", headers=headers, verify=False)
    comp = resp.json().get('components', [{}])[0]
    return comp.get('toVersion', '')

def skiphostsfromclusterofvendor(vendors, cluster_id):
    for hid in cluster_hosts_map.get(cluster_id, []):
        if all_hosts_map[hid].vendor in vendors:
            hosts_to_skip.append(hid)

def get_hosts_to_skip():
    return ','.join(sorted(set(hosts_to_skip)))

def return_custom_iso_path(vendor, cluster_name=None):
    # Single ISO per vendor: cluster context not shown
    if one_custom_iso_per_vendor:
        if vendor not in global_vendor_iso_map:
            print("\nNOTE: This Custom ISO will be used for all selected clusters with {} as vendor".format(vendor))
            global_vendor_iso_map[vendor] = input_iso_path(vendor)
        return global_vendor_iso_map[vendor]
    # Per-cluster ISO prompt
    return input_iso_path(vendor, cluster_name)


def input_iso_path(vendor, cluster_name=None):
    prompt = f"Enter path for {vendor} ISO"
    if cluster_name:
        prompt += f" (for cluster {cluster_name})"
    prompt += ": "
    for _ in range(3):
        path = input(prompt).strip()
        if check_if_iso_exists(path):
            return path
        print(f"Invalid ISO at {path}")
    sys.exit("Valid ISO path required")

# Property updates
def update_esx_upgrade_custom_upgrade_spec(outpath):
    """Set lcm.esx.upgrade.custom.image.spec to the provided JSON path in the LCM properties file."""
    try:
        with open(LCM_PROPERTIES_FILE, 'r+') as f:
            text = f.read()
            line = CUSTOM_IMAGE_PROPERTY + outpath
            if re.search(r'^lcm\.esx\.upgrade\.custom\.image\.spec=', text, flags=re.MULTILINE):
                text = re.sub(r'^lcm\.esx\.upgrade\.custom\.image\.spec=.*$', line, text, flags=re.MULTILINE)
            else:
                text += '' + line
            f.seek(0)
            f.write(text)
            f.truncate()
        logger.info("Updated custom image spec property")
    except Exception as exc:
        logger.error(f"Error updating custom image spec property: {exc}")

def update_esx_upgrades_skip_hosts_property():
    """Set esx.upgrade.skip.host.ids with a comma-separated list of host IDs to skip."""
    try:
        line = SKIP_HOST_PROPERTY + get_hosts_to_skip()
        with open(LCM_PROPERTIES_FILE, 'r+') as f:
            text = f.read()
            if re.search(r'^esx\.upgrade\.skip\.host\.ids=', text, flags=re.MULTILINE):
                text = re.sub(r'^esx\.upgrade\.skip\.host\.ids=.*$', line, text, flags=re.MULTILINE)
            else:
                text += '' + line
            f.seek(0)
            f.write(text)
            f.truncate()
        logger.info("Updated skip hosts property")
        logger.info(f"Skip hosts count: {len(set(hosts_to_skip))}")
    except Exception as exc:
        logger.error(f"Error updating skip hosts property: {exc}")

# Argument parsing
def parse_args():
    p = argparse.ArgumentParser(
    description=(
        "VMware Cloud Foundation - Generate custom ISO spec for ESXi cluster upgrades\n"
        "*** Note that this script only works for VUM/vLCM Baseline clusters ***"
    ),
    formatter_class=argparse.RawDescriptionHelpFormatter
)
    p.add_argument('-a', '--all', action='store_true', help='Automatically include all clusters in all domains')
    p.add_argument('-d', '--domain', metavar='<domain name>', help='Comma-separated domain name(s) to limit selection (works with --all or interactive mode)')
    return p.parse_args()

# Main
def main():
    args = parse_args()
    if os.geteuid() != 0:
        sys.exit("Root privileges required")
    print("NOTE: Previous changes may be overwritten \n")
    if input("Are you sure you want to run this script? (y/n): ").strip().lower() not in ('y','yes'):
        sys.exit("Exiting.")
    print(f"Log file: {log_path}\n")
    logger.info("=== Script start ===")

    # SSO prompt
    for i in range(3):
        user = input("Enter SSO User: ").strip()
        pwd = getpass("Enter SSO Password: ")
        if check_if_valid_sso(user, pwd):
            headers = get_auth_headers(user, pwd)
            break
        print(f"Invalid SSO credentials")
    else:
        sys.exit("Invalid SSO credentials")

    # ISO spec path
    spec_path = DEFAULT_CUSTOM_ISO_SPEC_PATH
    if input(f"\nDefault path where custom iso spec will be saved is {DEFAULT_CUSTOM_ISO_SPEC_PATH} \nDo you want to use a different path? (y/n): ").strip().lower() == 'y':
        pth = input("Enter custom spec path: ").strip()
        if check_if_directory_exists(pth):
            spec_path = pth
        else:
            sys.exit("Invalid directory")

    # Fetch inventory
    get_all_domain_names_in_env(headers)
    domains = list(domain_name_cluster_id_map.keys())
    get_all_clusters(headers)
    get_all_hosts(headers)
    logger.info(f"Inventory loaded: domains={len(domain_name_id)}, clusters={len(cluster_id_name)}, hosts={len(all_hosts_map)}")

    # ESX bundle selection via LCM API
    def list_esx_bundles(headers):
        resp = requests.get(f"{LOCAL_PATH}/v1/bundles?productType=ESX", headers=headers, verify=False)
        bundles = resp.json().get('elements', [])
        return [ (b['id'], b.get('components',[{}])[0].get('toVersion','')) for b in bundles ]

    bundles = list_esx_bundles(headers)
    if not bundles:
        sys.exit("No ESX bundles found via LCM API.")
    print("\nAvailable ESX bundles:")
    for idx, (bid, ver) in enumerate(bundles, 1):
        print(f"  {idx}) Bundle ID: {bid}, upgrade to ESX version: {ver}")
    while True:
        choice = input(f"Choose bundle [1-{len(bundles)}]: ").strip()
        if choice.isdigit():
            i = int(choice) - 1
            if 0 <= i < len(bundles):
                bundle_id, target_version = bundles[i]
                break
        print("Invalid selection, please try again.")

    # ISO per vendor
    global one_custom_iso_per_vendor
    one_custom_iso_per_vendor = input("\nDo you want to provide a single iso for each vendor? (y/n): ").strip().lower() == 'y'

        # Cluster selection
    selected = {}
    if args.all:
        # --domain optional: if provided, limit to those domains; otherwise include all domains
        if args.domain:
            domains_for_all = [x.strip() for x in args.domain.split(',') if x.strip()]
            missing = [d for d in domains_for_all if d not in domain_name_cluster_id_map]
            if missing:
                sys.exit("Unknown domain(s): " + ", ".join(missing))
        else:
            domains_for_all = list(domain_name_cluster_id_map.keys())
        selected = {d: domain_name_cluster_id_map[d][:] for d in domains_for_all}
        print("\n'--all' flag used. Selecting all clusters in domains: " + ", ".join(domains_for_all))
    else:
        # Default to interactive mode (domain + cluster selection)
        if args.domain:
            domains_for_batch = [d.strip() for d in args.domain.split(',') if d.strip()]
            missing = [d for d in domains_for_batch if d not in domain_name_cluster_id_map]
            if missing:
                sys.exit("Unknown domain(s): " + ", ".join(missing))
        else:
            domain_names = list(domain_name_cluster_id_map.keys())
            print("\nAvailable domains:")
            for i, dn in enumerate(domain_names, 1):
                print(f"{i}) {dn}")
            dsel = input("\nSelect domains by number (comma-separated) or type ALL: \n").strip()
            if dsel.lower() in ('all', 'a', '0'):
                domains_for_batch = domain_names
            else:
                domains_for_batch = []
                for part in dsel.split(','):
                    s = part.strip()
                    if s.isdigit():
                        idx = int(s) - 1
                        if 0 <= idx < len(domain_names):
                            domains_for_batch.append(domain_names[idx])
                    elif s in domain_names:
                        domains_for_batch.append(s)
            if not domains_for_batch:
                sys.exit("No valid domains selected.")
        options = [(cid, cluster_id_name.get(cid, cid), d) for d in domains_for_batch for cid in domain_name_cluster_id_map[d]]
        for idx, (cid, name, d) in enumerate(options, 1):
            print(f"{idx}) {name} ({cid}) in {d}")
        raw = input("Comma-separated cluster indices, IDs, or names (or ALL): ").strip()
        if raw.lower() in ('all', 'a', '0'):
            # Select all clusters in the selected domains
            for cid, _, d in options:
                selected.setdefault(d, []).append(cid)
        else:
            for ent in raw.split(','):
                key = ent.strip()
                if not key:
                    continue
                if key.isdigit():
                    i = int(key) - 1
                    if 0 <= i < len(options):
                        cid, _, d = options[i]
                    else:
                        print(f"  Skipping invalid index: {key}")
                        continue
                else:
                    match = next(((c, n, d0) for c, n, d0 in options if key == c or key == n), None)
                    if not match:
                        print(f"  Skipping unknown cluster: {key}")
                        continue
                    cid, _, d = match
                selected.setdefault(d, []).append(cid)
        if not selected:
            sys.exit("No clusters selected")

    # Build spec entries and count by vendor
    logger.info(f"Selected clusters: {sum(len(v) for v in selected.values())} across {len(selected)} domain(s)")
    vendor_count = {}
    for d, clist in selected.items():
        for cid in clist:
            cname = cluster_id_name.get(cid, cid)
            vendors = {all_hosts_map[h].vendor for h in cluster_hosts_map.get(cid, [])}
            if len(vendors) == 1:
                up = vendors.pop()
            else:
                for _ in range(3):
                    up = input(f"\nCluster {cname} has vendors {vendors}. Choose one: ").strip()
                    if up in vendors:
                        break
                else:
                    sys.exit(f"Invalid vendor for {cname}")
                skiphostsfromclusterofvendor([v for v in vendors if v != up], cid)
            vendor_count[up] = vendor_count.get(up, 0) + 1

            iso_path = return_custom_iso_path(up, cname)
            bid = bundle_id if bundle_id else input(f"Enter bundle ID for cluster {cname}: ")
            tv = target_version if target_version else get_esx_bundle_upgrade_to_version(bid, headers)

            esx_custom_image_spec_list.append(
                EsxCustomImageSpecObj(bid, tv, domain_name_id[d], iso_path, cid)
            )

    # Write JSON spec
    spec = {'esxCustomImageSpecList': [o.__dict__ for o in esx_custom_image_spec_list]}
    outpath = os.path.join(spec_path, CUSTOM_ISO_SPEC_FILENAME)
    with open(outpath, 'w') as f:
        json.dump(spec, f, indent=4)
    logger.info(f"Wrote custom ISO spec: {outpath} entries={len(esx_custom_image_spec_list)}")
    execute_cmd_locally(f"chmod 755 {outpath}")
    execute_cmd_locally(f"chown -R vcf_lcm:vcf {spec_path}")

    # Update LCM properties
    update_esx_upgrade_custom_upgrade_spec(outpath)
    print(f"\nSuccessfully updated {LCM_PROPERTIES_FILE} with custom ISO spec location\n")
    if hosts_to_skip:
        update_esx_upgrades_skip_hosts_property()
        print(f"Successfully updated skip hosts in LCM properties file located at {LCM_PROPERTIES_FILE}\n")
        print("Hosts that will be skipped are:\n")
        # Reverse map domain IDs to names
        domain_id_name = {v: k for k, v in domain_name_id.items()}
        for hid in hosts_to_skip:
            host = all_hosts_map.get(hid)
            if host:
                dom_name = domain_id_name.get(host.domain_id, host.domain_id)
                clust_name = cluster_id_name.get(host.cluster_id, host.cluster_id)
                print(f"- {host.fqdn} ({host.vendor}) in domain {dom_name}, cluster {clust_name}")

    # Summary
    summary = ', '.join([f"{cnt} {vendor} clusters" for vendor, cnt in vendor_count.items()])
    print(f"\nCustom ISO spec generated at {outpath}\n")
    print(f"Summary: {summary} have been added to custom ISO spec\n")

    # Restart prompt
    if input("Restart LCM service now? (y/n): ").strip().lower() in ('y','yes'):
        execute_cmd_locally('systemctl restart lcm')
        print('Restarting LCM service... \n')
        print('Waiting for service to start...\n')
        time.sleep(20)
        logger.info("LCM restarted")
        print("LCM service restarted")
        print('NOTE: PRIOR TO RUNNING THE UPGRADE, PLEASE RUN UPGRADE PRECHECK AND ENSURE IT PASSES \n')
    else:
        logger.info("LCM restart skipped")
        print("Skipping LCM service restart")
        print('NOTE: PRIOR TO RUNNING THE UPGRADE, PLEASE RUN UPGRADE PRECHECK AND ENSURE IT PASSES \n')

if __name__ == '__main__':
    main()
