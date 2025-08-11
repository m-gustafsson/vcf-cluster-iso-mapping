# VCF Cluster ISO Mapping

**TL;DR:** If you maintain VMware Cloud Foundation at scale, mapping clusters to custom ESXi ISOs by hand doesn’t scale. This post introduces a script that discovers your inventory, lets you pick an ESXi bundle, maps clusters to the right ISO (per‑vendor or per‑cluster), handles mixed‑vendor clusters by skipping non‑selected hosts, writes the JSON spec, and updates LCM properties and optionally restarts the LCM service for you.

> **Note**: This script only works for **vLCM Baseline/VUM mode** clusters.

---

## Quick links

- [Features](#features)
- [Requirements](#requirements)
- [Usage](#usage)
- [Examples](#examples)
- [JSON spec](#generated-json-spec)
---

## Features

- Discovers inventory (domains, clusters, hosts) from SDDC Manager API.
- Lists ESXi bundles (GET /v1/bundles?productType=ESX) and lets you pick one.
- Lets you choose single ISO per vendor or per‑cluster ISO with cluster context in the prompt.
- For **mixed‑vendor** clusters, you choose which vendor to upgrade; other vendor hosts are added to skip list.
  - Multiple ISOs cannot be mapped to a single cluster, a cluster with two vendors will have to be upgraded twice, once per vendor.
  - With mixed-vendor clusters, the hosts that are not mapped to a custom ISO will be skipped.
- Generates `generated_custom_iso_spec.json` with `esxCustomImageSpecList` entries per cluster.
- Updated LCM properties in `/opt/vmware/vcf/lcm/lcm-app/conf/application-prod.properties`
  - `lcm.esx.upgrade.custom.image.spec=<absolute_path_to_generated_custom_iso_spec.json>`
  - `esx.upgrade.skip.host.ids=<comma-separated-host-ids>` (only when applicable)
- Prints a summary per vendor and an optional LCM restart prompt.

---

## Requirements

- **VUM/vLCM Baseline mode. vLCM Image mode is not supported.**
- Run on SDDC Manager as root.
- Valid SSO credentials with permission to obtain a token.
- ISO files are present on disk and readable (e.g. `/nfs/vmware/vcf/nfs-mount/isos/*.iso`).

---

## Mixed-vendor handling

- **What if my cluster has 3 Dell + 1 HPE host?**

- You choose Dell → the single HPE host is added to `esx.upgrade.skip.host.ids`. The console prints each skipped host as:
```console
Hosts that will be skipped are:

– esxi-1.vrack.vsphere.local (HPE) in domain MGMT, cluster SDDC-Cluster1
```
- If you choose HPE → the three Dell hosts are added to `esx.upgrade.skip.host.ids`. The console output would be:
```console
Hosts that will be skipped are:

– esxi-2.vrack.vsphere.local (Dell) in domain MGMT, cluster SDDC-Cluster1
– esxi-3.vrack.vsphere.local (Dell) in domain MGMT, cluster SDDC-Cluster1
– esxi-4.vrack.vsphere.local (Dell) in domain MGMT, cluster SDDC-Cluster1
```

- **Mixed vendor clusters will need to be updated multiple times**

---

## Usage

Help:

```console
python cluster-iso-mapping.py -h
usage: cluster-iso-mapping.py [-h] [-a] [-d ]

VMware Cloud Foundation – Generate custom ISO spec for ESXi cluster upgrades

*** Note that this script only works for VUM/vLCM Baseline clusters. ***

options:
-h, --help show this help message and exit
-a, --all Automatically include all clusters in all domains
-d <domain name>, --domain <domain name>
Comma-separated domain name(s) to limit selection (works with --all or interactive mode)
```

**Modes**

- **Interactive (default)**: choose domains and clusters in the prompts.
- **All mode**: select every cluster (optionally limited by domain).

**Flags**

- `-a, --all` — automatically include all clusters (optionally scope with `--domain`).
- `-d, --domain domain` — comma-separated domain names, e.g. `--domain MGMT,sfo-w01` (works in both modes).

---

## Examples

**Interactive across all domains**

```bash
python cluster-iso-mapping.py
# Prompts you to select domains, then lists clusters to pick.
```

**Interactive for specific domains**

```bash
python cluster-iso-mapping.py --domain MGMT,sfo-w01
# Only clusters from MGMT and sfo-w01 domains are offered.
```

**All clusters across all domains**

```bash
python cluster-iso-mapping.py --all
# Selects every cluster in every domain
```

**All clusters limited to specific domains**

```bash
python cluster-iso-mapping.py --all --domain MGMT,sfo-w01
# Selects every cluster only in the listed domains
```

**Full output of above command**
```console
python cluster-iso-mapping.py --all --domain MGMT,sfo-w01

NOTE: Previous changes may be overwritten

Are you sure you want to run this script? (y/n): y
Enter SSO User: administrator@vsphere.local
Enter SSO Password:
Default path where custom iso spec will be saved is /opt/vmware/vcf/lcm/
Do you want to use a different path? (y/n): n

Available ESX bundles:
1) Bundle ID: d5e588e7-25e5-47ae-b9bc-49c1e80b9354, upgrade to ESX version: 8.0.3-24859861
2) Bundle ID: 4dfe77cb-eb8c-4aad-a3da-1c9c7fdc7851, upgrade to ESX version: 8.0.3-24784735
Choose bundle [1-2]: 2

Do you want to provide a single iso for each vendor? (y/n): y

‘--all’ flag used. Selecting all clusters in domains: MGMT, sfo-w01

Cluster SDDC-Cluster1 has vendors {‘VMware, Inc.’, ‘HPE’}. Choose one: VMware, Inc.

NOTE: This Custom ISO will be used for all selected clusters with VMware, Inc. as vendor
Enter path for VMware, Inc. ISO: /nfs/vmware/vcf/nfs-mount/isos/vmware.iso

NOTE: This Custom ISO will be used for all selected clusters with HPE as vendor
Enter path for HPE ISO: /nfs/vmware/vcf/nfs-mount/isos/hpe.iso

Cluster sfo-w01-cluster04 has vendors {‘Dell’, ‘HPE’}. Choose one: HPE

NOTE: This Custom ISO will be used for all selected clusters with Dell as vendor
Enter path for Dell ISO: /nfs/vmware/vcf/nfs-mount/isos/dell.iso

Successfully updated /opt/vmware/vcf/lcm/lcm-app/conf/application-prod.properties with custom ISO spec location

Successfully updated skip hosts in LCM properties file located at /opt/vmware/vcf/lcm/lcm-app/conf/application-prod.properties

Hosts that will be skipped are:

– esxi-2.vrack.vsphere.local (HPE) in domain MGMT, cluster SDDC-Cluster1
– esxi-3.vrack.vsphere.local (HPE) in domain MGMT, cluster SDDC-Cluster1
– esxi-1.vrack.vsphere.local (HPE) in domain MGMT, cluster SDDC-Cluster1
– sfo-w01-esx41.vrack.vsphere.local (Dell) in domain sfo-w01, cluster sfo-w01-cluster04
– sfo-w01-esx42.vrack.vsphere.local (Dell) in domain sfo-w01, cluster sfo-w01-cluster04
– sfo-w01-esx43.vrack.vsphere.local (Dell) in domain sfo-w01, cluster sfo-w01-cluster04
– sfo-w01-esx44.vrack.vsphere.local (Dell) in domain sfo-w01, cluster sfo-w01-cluster04
– sfo-w01-esx45.vrack.vsphere.local (Dell) in domain sfo-w01, cluster sfo-w01-cluster04

Custom ISO spec generated at /opt/vmware/vcf/lcm/generated_custom_iso_spec.json

Summary: 1 VMware, Inc. clusters, 4 HPE clusters, 6 Dell clusters have been added to custom ISO spec

Restart LCM service now? (y/n): y
Restarting LCM service…

Waiting for service to start…

LCM service restarted
NOTE: PRIOR TO RUNNING THE UPGRADE, PLEASE RUN UPGRADE PRECHECK AND ENSURE IT PASSES
```


---

## Generated JSON spec

Example (truncated):

```json
{
    "esxCustomImageSpecList": [
        {
            "bundleId": "4dfe77cb-eb8c-4aad-a3da-1c9c7fdc7851",
            "targetEsxVersion": "8.0.3-24784735",
            "useVcfBundle": false,
            "domainId": "183e7288-1a32-44f7-b2de-cdd2d84fba8a",
            "clusterId": "16be60bd-1c20-47b5-85d4-beed5c1dd91e",
            "customIsoAbsolutePath": "/nfs/vmware/vcf/nfs-mount/isos/vmware.iso"
        },
        {
            "bundleId": "4dfe77cb-eb8c-4aad-a3da-1c9c7fdc7851",
            "targetEsxVersion": "8.0.3-24784735",
            "useVcfBundle": false,
            "domainId": "0c69060c-5701-410e-8085-1c5ca697b313",
            "clusterId": "0ff829e0-0faf-4216-aea9-a12d4545fd39",
            "customIsoAbsolutePath": "/nfs/vmware/vcf/nfs-mount/isos/hpe.iso"
        },
        {
            "bundleId": "4dfe77cb-eb8c-4aad-a3da-1c9c7fdc7851",
            "targetEsxVersion": "8.0.3-24784735",
            "useVcfBundle": false,
            "domainId": "0c69060c-5701-410e-8085-1c5ca697b313",
            "clusterId": "19a30e28-9c5a-49f4-a4fd-27b3c52614d1",
            "customIsoAbsolutePath": "/nfs/vmware/vcf/nfs-mount/isos/hpe2.iso"
        },
        {
            "bundleId": "4dfe77cb-eb8c-4aad-a3da-1c9c7fdc7851",
            "targetEsxVersion": "8.0.3-24784735",
            "useVcfBundle": false,
            "domainId": "0c69060c-5701-410e-8085-1c5ca697b313",
            "clusterId": "395bea55-bed1-4573-bc54-7d0561fbcbb3",
            "customIsoAbsolutePath": "/nfs/vmware/vcf/nfs-mount/isos/hpe3.iso"
        },
        {
            "bundleId": "4dfe77cb-eb8c-4aad-a3da-1c9c7fdc7851",
            "targetEsxVersion": "8.0.3-24784735",
            "useVcfBundle": false,
            "domainId": "0c69060c-5701-410e-8085-1c5ca697b313",
            "clusterId": "4865dc8c-692f-48e1-9593-ffba5fd2cf2e",
            "customIsoAbsolutePath": "/nfs/vmware/vcf/nfs-mount/isos/dell.iso"
        },
        {
            "bundleId": "4dfe77cb-eb8c-4aad-a3da-1c9c7fdc7851",
            "targetEsxVersion": "8.0.3-24784735",
            "useVcfBundle": false,
            "domainId": "0c69060c-5701-410e-8085-1c5ca697b313",
            "clusterId": "ae7a1d54-a4db-4fb5-ace7-2df4d198eeb4",
            "customIsoAbsolutePath": "/nfs/vmware/vcf/nfs-mount/isos/dell2.iso"
        }
    ]
}
```

---

> Authored by Martin Gustafsson — [https://martingustafsson.com](https://martingustafsson.com)\
> Contributions welcome. Open an issue or PR with details of your environment and logs.

<p align="center">
  <a href="https://www.buymeacoffee.com/mgustafsson" target="_blank" rel="noopener noreferrer">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png"
         alt="Buy Me A Coffee" height="50">
  </a>
</p>

