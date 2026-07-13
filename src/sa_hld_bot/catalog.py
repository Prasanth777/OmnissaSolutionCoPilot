from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


TECHZONE_DOMAIN = "techzone.omnissa.com"
TECHZONE_SITEMAP_URL = f"https://{TECHZONE_DOMAIN}/sitemap.xml"
UNKNOWN = "Unknown / to be confirmed"


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    help_text: str = ""
    options: tuple[str, ...] = ()
    multi_select: bool = False
    allow_custom: bool = True
    show_if: tuple[tuple[str, str], ...] = ()
    show_if_all: tuple[tuple[str, str], ...] = ()
    source_title: str = ""
    source_url: str = ""
    source_section_title: str = ""
    source_query: str = ""


@dataclass(frozen=True)
class Resource:
    title: str
    url: str
    summary: str
    design_focus: tuple[str, ...] = ()


@dataclass(frozen=True)
class Product:
    key: str
    title: str
    family: str
    summary: str
    resource: Resource
    follow_up_questions: tuple[Question, ...] = ()
    related_resources: tuple[Resource, ...] = ()


def is_allowed_techzone_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.netloc == TECHZONE_DOMAIN


def q(
    key: str,
    prompt: str,
    help_text: str = "",
    options: tuple[str, ...] = (),
    multi_select: bool = False,
    allow_custom: bool = True,
    show_if: tuple[tuple[str, str], ...] = (),
    show_if_all: tuple[tuple[str, str], ...] = (),
    source_url: str = "",
    source_title: str = "",
) -> Question:
    return Question(
        key=key,
        prompt=prompt,
        help_text=help_text,
        options=options,
        multi_select=multi_select,
        allow_custom=allow_custom,
        show_if=show_if,
        show_if_all=show_if_all,
        source_url=source_url,
        source_title=source_title,
    )


COMMON_QUESTIONS: tuple[Question, ...] = (
    q("customer_name", "What is the customer name for this HLD?", "Use the legal/entity name you want on the document.", ()),
    q("project_name", "What is the project or service name?", "Used on the DOCX cover, headers, and document reference section.", ()),
    q("document_version", "What document version should appear in the HLD?", "Use the current draft or release version.", ("0.1 - Draft", "0.5 - Review Draft", "1.0 - Final", UNKNOWN)),
    q("prepared_by", "Who is preparing this HLD?", "Name or team shown in the key contacts and metadata sections.", ()),
    q("customer_contacts", "Who are the key customer contacts?", "List sponsor, technical owner, security/network contacts, or mark unknown.", (UNKNOWN,)),
    q("reviewers", "Who should review or approve this design?", "Used in the review and acceptance section.", ("Customer technical approver", "Security/network approver", "Operations approver", UNKNOWN), multi_select=True),
    q("industry", "Which industry best matches the customer?", "Pick the closest business domain to tailor architecture language.", ("Healthcare", "Financial Services", "Manufacturing", "Public Sector", "Retail", "Technology", UNKNOWN)),
    q("project_scope", "What is the primary business objective for this initiative?", "Focus on the most important outcome this design must deliver.", ("Modernize EUC platform", "Enable secure remote work", "Improve user experience", "Reduce operational cost", "Strengthen security posture", UNKNOWN)),
    q("business_drivers", "What are the main business drivers?", "Capture the drivers that should appear in the business requirements table.", ("Support hybrid work", "Replace legacy VDI/app platform", "Improve clinical/user experience", "Improve security and compliance", "Reduce EUC operating cost", UNKNOWN), multi_select=True),
    q("success_criteria", "What success criteria should the HLD capture?", "Examples: availability target, user experience goal, security approval, migration outcome.", ("Stable remote access", "Improved login and launch experience", "High availability for critical users", "Security/compliance approval", "Operational handover ready", UNKNOWN), multi_select=True),
    q("in_scope", "What is in scope for this HLD?", "List products, user groups, sites, workloads, or integration areas.", ("Core Horizon platform", "External access", "Application delivery", "User environment management", "Operations and DR", UNKNOWN), multi_select=True),
    q("out_of_scope", "What is explicitly out of scope?", "This prevents the HLD from implying ownership of unrelated work.", ("Endpoint refresh", "Application remediation", "Network redesign", "Identity tenant redesign", "Detailed implementation runbook", UNKNOWN), multi_select=True),
    q("users_personas", "Which user personas are in scope?", "Choose the dominant end-user profile for sizing and workload patterns.", ("Task workers", "Knowledge workers", "Developers", "Clinicians", "Contact center users", "Mixed workforce", UNKNOWN)),
    q("workload_concurrency", "What is the expected user scale or concurrency?", "Use a number, range, or unknown so sizing tables can be populated honestly.", ("Up to 500 concurrent users", "500-2,000 concurrent users", "2,000+ concurrent users", UNKNOWN)),
    q("hosting_strategy", "What hosting strategy should this design target?", "Select the intended landing zone for management and workloads.", ("On-premises", "Cloud", "Hybrid", UNKNOWN)),
    q("site_topology", "What is the site topology for this deployment?", "Single site is most common. Multi-site introduces DR and replication considerations.", ("Single site", "Multi-site", UNKNOWN)),
    q("primary_site", "What is the primary site or region?", "Used in site topology, networking, and recovery sections.", (UNKNOWN,)),
    q("secondary_sites", "What secondary sites, branches, or DR regions are in scope?", "List site names/regions or mark unknown.", ("Not applicable", UNKNOWN), show_if=(("site_topology", "Multi-site"),)),
    q("access_type", "Who needs access to the environment?", "This drives whether UAG, load balancers, and FQDN strategy are required.", ("Internal users only", "External users only", "Both internal and external", UNKNOWN)),
    q("load_balancer", "Do you have a load balancer available for this deployment?", "Required for UAG HA and Connection Server redundancy.", ("Yes - provide name below", "Built-in load balancing"), show_if=(("access_type", "External users only"), ("access_type", "Both internal and external"))),
    q("load_balancer_name", "What is the load balancer name or platform?", "Capture the customer load balancer, for example F5, NetScaler, NSX ALB, or the platform/service name.", ()),
    q("load_balancer_placement", "Where will the load balancer be placed?", "This drives whether UAG, Connection Server, or both paths are shown in the HLD.", ("In front of UAG", "In front of Connection Server", "Both")),
    q("fqdn_strategy", "Will you use a single FQDN for both internal and external users?", "Single FQDN requires Split DNS between internal and public resolution.", ("Yes - single FQDN with Split DNS", "No - separate internal and external URLs", UNKNOWN), show_if=(("access_type", "Both internal and external"),)),
    q("cert_type", "What type of SSL certificate will be used?", "UAG terminates TLS and needs the private key.", ("Wildcard certificate (*.domain.com)", "SAN / multi-domain certificate", "No certificate yet - need to procure", UNKNOWN), show_if=(("access_type", "External users only"), ("access_type", "Both internal and external"))),
    q("certificate_owner", "Who owns certificate procurement and renewal?", "Used in security and operations responsibilities.", ("Customer security team", "Customer network team", "Managed service provider", UNKNOWN), show_if=(("access_type", "External users only"), ("access_type", "Both internal and external"))),
    q("horizon_dmz_design", "Which DMZ design should the HLD capture?", "Choose the edge security layout so matching UAG diagrams are selected.", ("Single DMZ", "Double DMZ", "Per-site DMZ pair", "No DMZ / internal only", UNKNOWN), show_if=(("access_type", "External users only"), ("access_type", "Both internal and external"))),
    q("identity_source", "What is the identity source for authentication and access?", "Pick the user directory / identity provider used by the customer.", ("Active Directory", "Entra ID", "Hybrid AD + Entra ID", UNKNOWN)),
    q("dns_dhcp_ntp", "What DNS, DHCP, and NTP assumptions should be captured?", "Examples: enterprise DNS, DHCP scopes, split DNS, NTP source.", ("Existing enterprise DNS/DHCP/NTP", "New DNS/DHCP entries required", "Split DNS required", UNKNOWN)),
    q("network_segments", "Which network segments, VLANs, or subnets are relevant?", "Used for networking requirements and open items. Exact IPs can be omitted.", ("Management", "DMZ", "VDI workloads", "RDSH workloads", "User/endpoint networks", UNKNOWN), multi_select=True),
    q("firewall_ports", "What firewall or port assumptions should be noted?", "Capture known firewall boundaries or Tech Zone port guidance.", ("Use Omnissa Tech Zone Horizon port guidance", "Internal firewall review required", "External/DMZ firewall review required", UNKNOWN), multi_select=True),
    q("security_requirements", "Which security baseline is required?", "Select the control profile that best fits compliance and risk expectations.", ("MFA + conditional access", "Compliance-first (audit heavy)", "Data loss prevention focus", "High security segmentation", UNKNOWN)),
    q("mfa_required", "Is multi-factor authentication (MFA) required?", "Internal uses Connection Server, external uses UAG; non-native providers may add Access.", ("Yes", "No", UNKNOWN)),
    q("mfa_provider", "Which MFA provider is in use?", "SAML-based and RADIUS providers follow different integration patterns.", ("Entra ID (SAML)", "Okta (SAML)", "RADIUS / RSA token", "Ping ID (SAML)", UNKNOWN), show_if=(("mfa_required", "Yes"),)),
    q("availability_requirements", "What availability target should the architecture meet?", "Choose the resiliency expectation for management and workload tiers.", ("Single site", "N+1 within region", "Multi-site active/passive", "Multi-site active/active", UNKNOWN)),
    q("backup_requirements", "What backup expectations should the HLD include?", "Capture platform backup responsibilities and any recovery expectations.", ("Backup Horizon configuration and databases", "Backup golden images/templates", "Customer backup platform to be used", UNKNOWN), multi_select=True),
    q("dr_scenarios", "Which disaster recovery scenarios should be covered?", "Used in the business continuity and recovery section.", ("Single component failure", "Site failure", "Cloud/service dependency outage", "Network edge failure", UNKNOWN), multi_select=True),
    q("operations_owner", "Who will operate the platform after deployment?", "Used in operational model and acceptance sections.", ("Customer EUC operations", "Customer infrastructure operations", "Managed service provider", "Shared operations model", UNKNOWN)),
    q("monitoring_logging", "What monitoring and logging approach should be captured?", "Examples: Horizon monitoring, SIEM integration, event database, log retention.", ("Horizon/Event database monitoring", "SIEM integration required", "Existing monitoring platform", UNKNOWN), multi_select=True),
    q("rbac_model", "What RBAC or administration model should be used?", "Used in the security standards section.", ("Least privilege role groups", "Separate operations and security roles", "Customer standard admin groups", UNKNOWN), multi_select=True),
    q("antivirus_hardening", "What antivirus or hardening requirements should be captured?", "Examples: AV exclusions, security baseline, image hardening.", ("Use Omnissa recommended exclusions", "Customer AV baseline applies", "Security hardening review required", UNKNOWN), multi_select=True),
    q("risks", "What risks should the HLD call out?", "List risks such as missing network details, certificate readiness, sizing uncertainty, or dependency owners.", ("Network/firewall dependencies", "Certificate readiness", "Sizing assumptions not validated", "Identity/MFA dependency", UNKNOWN), multi_select=True),
    q("constraints", "What constraints should the HLD call out?", "Capture constraints such as timelines, platform standards, limited bandwidth, or procurement dependencies.", ("Existing network architecture", "Customer security standards", "Procurement/timeline dependency", "Limited branch bandwidth", UNKNOWN), multi_select=True),
    q("open_items", "What open items remain to be confirmed?", "These will appear as open design items rather than guessed content.", ("IP addressing/subnets", "FQDNs and certificates", "Load balancer VIPs", "Final sizing", UNKNOWN), multi_select=True),
    q("assumptions", "Any dependencies, assumptions, or constraints we must capture?", "Include known blockers, prerequisites, or external teams/services.", ("Customer will provide required network services", "Customer will provide certificates", "Firewall rules will follow Tech Zone guidance", UNKNOWN), multi_select=True),
)


HORIZON_8 = Product(
    key="horizon_8",
    title="Horizon 8",
    family="Horizon",
    summary="Customer-managed Horizon deployment for VDI and published applications.",
    resource=Resource("What Is Omnissa Horizon?", "https://techzone.omnissa.com/resource/what-omnissa-horizon", "Horizon product family introduction and selection guidance."),
    related_resources=(
        Resource("Horizon 8 Architecture", "https://techzone.omnissa.com/resource/horizon-8-architecture", "Core Horizon 8 architecture design guidance."),
        Resource("Horizon 8 Configuration", "https://techzone.omnissa.com/resource/horizon-8-configuration", "Horizon 8 configuration guidance."),
        Resource("Reference Architecture VM Specifications", "https://techzone.omnissa.com/resource/reference-architecture-vm-specifications", "VM sizing and reference specifications."),
        Resource("Network Ports in Horizon 8", "https://techzone.omnissa.com/resource/network-ports-horizon-8", "Network and firewall port guidance."),
        Resource("Unified Access Gateway Architecture", "https://techzone.omnissa.com/resource/unified-access-gateway-architecture", "Secure remote access architecture patterns."),
        Resource("Horizon 8 on VMware Cloud on AWS Architecture", "https://techzone.omnissa.com/resource/horizon-8-vmware-cloud-aws-architecture", "Reference architecture for Horizon 8 on VMC on AWS."),
        Resource("Horizon 8 on Azure VMware Solution Architecture", "https://techzone.omnissa.com/resource/horizon-8-azure-vmware-solution-architecture", "Reference architecture for Horizon 8 on AVS."),
        Resource("Horizon 8 on Google Cloud VMware Engine Architecture", "https://techzone.omnissa.com/resource/horizon-8-google-cloud-vmware-engine-architecture", "Reference architecture for Horizon 8 on GCVE."),
        Resource("Horizon 8 on Oracle Cloud VMware Solution Architecture", "https://techzone.omnissa.com/resource/horizon-8-oracle-cloud-vmware-solution-architecture", "Reference architecture for Horizon 8 on OCVS."),
        Resource("Horizon 8 on Alibaba Cloud VMware Service Architecture", "https://techzone.omnissa.com/resource/horizon-8-alibaba-cloud-vmware-service-architecture", "Reference architecture for Horizon 8 on ACVS."),
    ),
    follow_up_questions=(
        q("horizon_use_cases", "Which Horizon 8 workloads are required?", "Select one or more desktop/app delivery workload patterns.", ("Pooled VDI", "Persistent VDI", "Published apps", "Mixed VDI + published apps", UNKNOWN), multi_select=True),
        q("horizon_pool_model", "Which image families should be documented for desktop or server workloads?", "This captures the desktop/server OS image inputs for the HLD, not the workload type.", ("Desktop images", "Server images", "Both desktop and server images", UNKNOWN)),
        q("desktop_image_version", "Which Windows desktop image version should be captured?", "Provide the Windows 11 release/build where known, or mark it to be confirmed.", ("Windows 11 24H2", "Windows 11 23H2", "Windows 11 - provide version", UNKNOWN)),
        q("server_image_version", "Which Windows Server image version should be captured?", "Select the server OS image version for RDSH or server-hosted workloads.", ("Windows Server 2025", "Windows Server 2022", "Windows Server 2016", UNKNOWN)),
        q("horizon_pod_block_model", "Which Horizon pod and block model should the HLD assume?", "This drives pod/block, single-site, and multi-site architecture diagrams.", ("Single-site scaled pod", "Multi-site pod architecture", "Cloud Pod Architecture", UNKNOWN)),
        q("horizon_connection_server_count", "How many Horizon Connection Servers are planned per pod or site?", "Use a count/range or mark unknown.", ("2 per pod/site", "3+ per pod/site", UNKNOWN)),
        q("horizon_external_access", "How should external access be handled?", "Select the edge access model for remote users.", ("No external access", "UAG in DMZ", "UAG + load balancer", "Zero Trust edge", UNKNOWN), show_if=(("access_type", "External users only"), ("access_type", "Both internal and external"))),
        q("horizon_access_topology", "Which remote access topology best fits the customer?", "This helps choose the right external access diagrams.", ("Without load balancer", "With load balancer", "Global load-balanced edge", UNKNOWN), show_if=(("access_type", "External users only"), ("access_type", "Both internal and external"))),
        q("horizon_8_arch_track", "Which Horizon 8 architecture path best matches your target platform?", "Align to the right Tech Zone architecture.", ("Horizon 8 core architecture", "Horizon 8 on VMware Cloud on AWS", "Horizon 8 on Azure VMware Solution", "Horizon 8 on Google Cloud VMware Engine", "Horizon 8 on Oracle Cloud VMware Solution", "Horizon 8 on Alibaba Cloud VMware Service", UNKNOWN)),
        q("horizon_8_design_focus", "Which Horizon 8 design section should be prioritized?", "Select the section where you want the most detail.", ("Management components and control plane", "Desktop/RDS host workload pools", "Network, UAG, and edge access", "Availability, scale, and DR", UNKNOWN)),
        q("horizon_database_events", "What Horizon event database approach should be captured?", "Used in integration and operations sections.", ("Existing SQL platform", "New SQL database", "Not required for this phase", UNKNOWN)),
        q("horizon_golden_image", "What golden image or parent VM strategy should be captured?", "Used in desktop/RDSH build and operations sections.", ("Single standard golden image", "Separate desktop and RDSH images", "Customer-managed image lifecycle", UNKNOWN)),
    ),
)


HORIZON_CLOUD = Product(
    key="horizon_cloud",
    title="Horizon Cloud",
    family="Horizon",
    summary="Cloud-delivered desktop and app service with Horizon control plane.",
    resource=Resource("What Is Omnissa Horizon?", "https://techzone.omnissa.com/resource/what-omnissa-horizon", "Horizon product family context."),
    related_resources=(
        Resource("Horizon Cloud Service Next-Gen Architecture", "https://techzone.omnissa.com/resource/horizon-cloud-service-next-gen-architecture", "Next-gen Horizon Cloud reference architecture."),
        Resource("Horizon Cloud Service Next-Gen Configuration", "https://techzone.omnissa.com/resource/horizon-cloud-service-next-gen-configuration", "Configuration patterns."),
        Resource("Horizon Cloud Service Security Overview", "https://techzone.omnissa.com/resource/horizon-cloud-service-security-overview", "Security overview."),
    ),
    follow_up_questions=(
        q("horizon_cloud_provider", "Which cloud platform will host Horizon Cloud workloads?", "Choose the primary hyperscaler target.", ("Microsoft Azure", "AWS", "Google Cloud", UNKNOWN)),
        q("horizon_cloud_use_cases", "What is the main Horizon Cloud adoption scenario?", "Pick the primary reason for using Horizon Cloud.", ("DaaS migration", "Cloud burst", "Net new desktop service", "Hybrid control plane", UNKNOWN)),
        q("horizon_cloud_connectivity", "Which connectivity architecture is preferred?", "Select the expected network interconnect model.", ("Private connectivity", "Internet + UAG", "Hub-and-spoke network", UNKNOWN)),
        q("horizon_cloud_arch_track", "Which Horizon Cloud architecture track should we follow?", "Choose the baseline for solution decisions.", ("Horizon Cloud next-gen architecture", "Horizon Cloud security architecture focus", "Horizon Cloud connectivity-first architecture", UNKNOWN)),
        q("horizon_cloud_design_focus", "Which Horizon Cloud section needs the deepest design detail?", "Pick the section requiring stronger depth.", ("Pod/control plane and tenant model", "Desktop/app workload delivery", "Identity and access integration", "Operations, monitoring, and resilience", UNKNOWN)),
    ),
)


APP_VOLUMES = Product(
    key="app_volumes",
    title="App Volumes",
    family="Horizon",
    summary="Application packaging and dynamic delivery for VDI/app sessions.",
    resource=Resource("What Is Omnissa App Volumes?", "https://techzone.omnissa.com/resource/what-omnissa-app-volumes", "App Volumes overview."),
    related_resources=(Resource("App Volumes Architecture", "https://techzone.omnissa.com/resource/app-volumes-architecture", "Reference architecture for App Volumes."),),
    follow_up_questions=(
        q("app_volumes_scope", "How do you plan to use App Volumes?", "Select the dominant app delivery/lifecycle use case.", ("App attach for non-persistent VDI", "Dynamic app entitlements", "Lifecycle simplification", UNKNOWN)),
        q("app_volumes_arch_track", "Which App Volumes architecture path best fits this project?", "Choose the architecture/design baseline.", ("App Volumes architecture baseline", "App Volumes packaging and lifecycle operations", "App Volumes with Horizon published apps on-demand", UNKNOWN)),
        q("app_volumes_design_focus", "Which App Volumes section should be prioritized?", "Pick the area where detailed design guidance is most needed.", ("Application packaging and capture", "Assignment and entitlement model", "Storage/performance and scale", "Operational governance and lifecycle", UNKNOWN)),
        q("app_volumes_storage", "What App Volumes storage approach should be captured?", "Used in storage group and package delivery design tables.", ("Shared vSphere datastore", "Replicated datastore per site", "Storage groups", UNKNOWN)),
        q("app_volumes_database", "What App Volumes database approach should be captured?", "Used in App Volumes component and availability tables.", ("Existing SQL platform", "SQL Always On / HA database", "New standalone SQL database", UNKNOWN)),
    ),
)


DEM = Product(
    key="dynamic_environment_manager",
    title="Dynamic Environment Manager",
    family="Horizon",
    summary="Context-aware user profile and environment policy management.",
    resource=Resource("Dynamic Environment Manager Architecture", "https://techzone.omnissa.com/resource/dynamic-environment-manager-architecture", "DEM reference architecture."),
    related_resources=(Resource("What Is Omnissa Horizon?", "https://techzone.omnissa.com/resource/what-omnissa-horizon", "Horizon portfolio context including DEM."),),
    follow_up_questions=(
        q("dem_scope", "What should DEM primarily manage?", "Pick the most important user environment policy scope.", ("Profile management", "Context-based policies", "Printer and drive mapping", "All of the above", UNKNOWN)),
        q("dem_arch_track", "Which DEM architecture/design path should we use?", "Choose the best-fit track for user environment management.", ("DEM architecture baseline", "DEM configuration and policy operations", "DEM with app personalization focus", UNKNOWN)),
        q("dem_design_focus", "Which DEM section should be designed in detail?", "Pick the area requiring deepest implementation planning.", ("Profile archives and configuration shares", "Context-based conditions and policies", "User personalization and app settings", "Operations and troubleshooting", UNKNOWN)),
        q("dem_file_shares", "What DEM file share approach should be captured?", "Used for configuration share, profile archive share, and multi-site design.", ("Existing SMB file servers", "New highly available SMB shares", "Per-site shares with replication", UNKNOWN)),
        q("dem_profile_strategy", "What user profile strategy should be captured?", "Used in DEM and FSLogix design sections.", ("DEM profile archives", "FSLogix profile containers", "DEM + FSLogix", UNKNOWN)),
    ),
)


WORKSPACE_ONE_UEM = Product(
    key="workspace_one_uem",
    title="Workspace ONE UEM",
    family="Workspace ONE",
    summary="Unified endpoint management across modern device platforms.",
    resource=Resource("What Is Workspace ONE?", "https://techzone.omnissa.com/resource/what-workspace-one", "Workspace ONE platform overview."),
    related_resources=(Resource("Workspace ONE UEM Architecture", "https://techzone.omnissa.com/resource/workspace-one-uem-architecture", "Reference architecture for Workspace ONE UEM."),),
    follow_up_questions=(
        q("uem_platforms", "Which device platforms are in scope for UEM?", "Choose the platform mix you need to manage.", ("Windows + macOS", "iOS + Android", "All major platforms", "Rugged/shared devices", UNKNOWN)),
        q("uem_ownership", "What is the device ownership model?", "Select the endpoint ownership pattern used by the customer.", ("Corporate-owned", "BYOD", "COPE", "Mixed", UNKNOWN)),
        q("uem_security", "Which UEM security control theme matters most?", "Choose the strongest management/security priority.", ("Compliance + conditional launch", "Patch and vulnerability baseline", "Application control", UNKNOWN)),
        q("uem_arch_track", "Which Workspace ONE UEM architecture path should guide this HLD?", "Choose the architecture/design baseline.", ("Workspace ONE UEM architecture baseline", "Workspace ONE UEM modern SaaS architecture", "Workspace ONE UEM configuration-first path", UNKNOWN)),
        q("uem_design_focus", "Which UEM design section should be prioritized?", "Pick the section that needs the strongest technical depth.", ("Enrollment and onboarding workflows", "Profiles, compliance, and conditional controls", "Application lifecycle and software distribution", "Operations, reporting, and governance", UNKNOWN)),
    ),
)


OMNISSA_ACCESS = Product(
    key="omnissa_access",
    title="Omnissa Access",
    family="Workspace ONE",
    summary="Identity, SSO, conditional access, and app catalog services.",
    resource=Resource("Workspace ONE Access Architecture", "https://techzone.omnissa.com/resource/workspace-one-access-architecture", "Reference architecture for Workspace ONE Access."),
    related_resources=(Resource("Workspace ONE Access Configuration", "https://techzone.omnissa.com/resource/workspace-one-access-configuration", "Configuration reference."),),
    follow_up_questions=(
        q("access_authentication", "Which authentication pattern should Omnissa Access support?", "Choose the primary identity pattern.", ("SAML federation", "MFA and conditional access", "Certificate-based access", "Passwordless / modern auth", UNKNOWN)),
        q("access_catalog", "Which app catalog scope is required?", "Select app presentation requirements.", ("SaaS/web apps", "Virtual apps from Horizon", "Unified catalog all apps", UNKNOWN)),
        q("access_arch_track", "Which Access architecture path should guide the HLD?", "Choose architecture/configuration baseline.", ("Workspace ONE Access architecture baseline", "Workspace ONE Access configuration-first path", "Zero Trust and conditional access focus", UNKNOWN)),
        q("access_design_focus", "Which Access design section should be prioritized?", "Pick the area requiring strongest depth.", ("Authentication methods", "Directory sync and identity source", "Application catalog and virtual apps", "Access policies and compliance", UNKNOWN)),
    ),
)


UAG = Product(
    key="unified_access_gateway",
    title="Unified Access Gateway",
    family="Workspace ONE",
    summary="Secure edge access for Horizon and Workspace ONE services.",
    resource=Resource("Unified Access Gateway Architecture", "https://techzone.omnissa.com/resource/unified-access-gateway-architecture", "UAG architecture patterns."),
    related_resources=(
        Resource("Deploying Unified Access Gateway", "https://techzone.omnissa.com/resource/deploying-unified-access-gateway", "UAG deployment guidance."),
        Resource("Load Balancing UAG for Horizon", "https://techzone.omnissa.com/resource/load-balancing-unified-access-gateway-horizon", "Load balancing and scale design patterns."),
    ),
    follow_up_questions=(
        q("uag_nic_config", "How many NICs should the UAG be deployed with?", "2 NIC is common; 3 NIC separates management; 1 NIC is not recommended for production.", ("2 NIC - Recommended (DMZ NIC + Internal NIC)", "3 NIC - Most secure (DMZ + Internal + Management NICs)", "1 NIC - Not recommended for production", UNKNOWN)),
        q("uag_services", "Which services should Unified Access Gateway publish?", "Choose the edge service mix required.", ("Horizon edge", "Tunnel and web reverse proxy", "Content services edge", "Combined services", UNKNOWN)),
        q("uag_edge_pattern", "What edge deployment pattern should be used?", "Select the scale/HA pattern for UAG placement.", ("Single DMZ pair", "Per-site DMZ pair", "Global load-balanced edge", UNKNOWN)),
        q("uag_arch_track", "Which UAG architecture track should guide the design?", "Choose the architecture/deployment baseline.", ("UAG architecture baseline", "UAG high-availability architecture", "UAG load-balanced global edge architecture", UNKNOWN)),
        q("uag_design_focus", "Which UAG design section should be prioritized?", "Pick the edge topic that needs detailed decisions.", ("DMZ placement and network segmentation", "Protocol/service edge configuration", "High availability and scale-out topology", "Certificate, security hardening, and operations", UNKNOWN)),
    ),
)


PRODUCTS: dict[str, Product] = {item.key: item for item in (HORIZON_8, HORIZON_CLOUD, APP_VOLUMES, DEM, WORKSPACE_ONE_UEM, OMNISSA_ACCESS, UAG)}

FAMILY_CHOICES: dict[str, tuple[str, ...]] = {
    "Horizon": ("horizon_8", "horizon_cloud", "app_volumes", "dynamic_environment_manager"),
    "Workspace ONE": ("workspace_one_uem", "omnissa_access", "unified_access_gateway"),
}


def normalize_answer(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _answer(answers: dict, key: str) -> str:
    return normalize_answer(answers.get(key)).lower()


def _is_internal_only(answers: dict) -> bool:
    return _answer(answers, "access_type") == "internal users only"


def _is_external_access(answers: dict) -> bool:
    access = _answer(answers, "access_type")
    return access in {"external users only", "both internal and external"}


def _is_single_site(answers: dict) -> bool:
    return _answer(answers, "site_topology") == "single site"


def _is_multi_site(answers: dict) -> bool:
    site = _answer(answers, "site_topology")
    availability = _answer(answers, "availability_requirements")
    return site == "multi-site" or "multi-site" in availability or "active/passive" in availability or "active/active" in availability


def required_questions(selected_product_keys: list[str]) -> list[Question]:
    questions = list(COMMON_QUESTIONS)
    for key in selected_product_keys:
        product = PRODUCTS.get(key)
        if product:
            questions.extend(product.follow_up_questions)
    return questions


def _essential_question_keys(selected_product_keys: list[str], answers: dict) -> set[str]:
    keys = {
        "customer_name",
        "project_name",
        "workload_concurrency",
        "hosting_strategy",
        "site_topology",
        "access_type",
        "identity_source",
        "mfa_required",
        "mfa_provider",
        "availability_requirements",
    }
    if _is_external_access(answers):
        keys.update({"load_balancer", "fqdn_strategy", "cert_type", "horizon_dmz_design"})
        if _answer(answers, "load_balancer").startswith("yes"):
            keys.update({"load_balancer_name", "load_balancer_placement"})
    if _is_multi_site(answers):
        keys.update({"secondary_sites", "dr_scenarios"})

    if "horizon_8" in selected_product_keys:
        keys.update({
            "horizon_use_cases",
            "horizon_pool_model",
            "desktop_image_version",
            "server_image_version",
            "horizon_pod_block_model",
            "horizon_external_access",
            "horizon_access_topology",
            "horizon_dmz_design",
            "horizon_8_arch_track",
        })
    if "horizon_cloud" in selected_product_keys:
        keys.update({
            "horizon_cloud_provider",
            "horizon_cloud_use_cases",
            "horizon_cloud_connectivity",
            "horizon_cloud_arch_track",
        })
    if "app_volumes" in selected_product_keys:
        keys.update({"app_volumes_scope", "app_volumes_storage", "app_volumes_database"})
    if "dynamic_environment_manager" in selected_product_keys:
        keys.update({"dem_scope", "dem_file_shares", "dem_profile_strategy"})
    if "workspace_one_uem" in selected_product_keys:
        keys.update({"uem_platforms", "uem_ownership", "uem_security", "uem_arch_track"})
    if "omnissa_access" in selected_product_keys:
        keys.update({"access_authentication", "access_catalog", "access_arch_track"})
    if "unified_access_gateway" in selected_product_keys or _is_external_access(answers):
        keys.update({"uag_nic_config", "uag_services", "uag_edge_pattern"})
    return keys


QUESTION_PRIORITY = [
    "customer_name",
    "project_name",
    "hosting_strategy",
    "access_type",
    "site_topology",
    "secondary_sites",
    "mfa_required",
    "mfa_provider",
    "identity_source",
    "workload_concurrency",
    "availability_requirements",
    "load_balancer",
    "load_balancer_name",
    "load_balancer_placement",
    "fqdn_strategy",
    "cert_type",
    "horizon_cloud_provider",
    "horizon_cloud_use_cases",
    "horizon_cloud_connectivity",
    "horizon_cloud_arch_track",
    "horizon_use_cases",
    "horizon_pool_model",
    "desktop_image_version",
    "server_image_version",
    "horizon_pod_block_model",
    "horizon_8_arch_track",
    "horizon_external_access",
    "horizon_access_topology",
    "horizon_dmz_design",
    "uag_nic_config",
    "uag_services",
    "uag_edge_pattern",
    "app_volumes_scope",
    "app_volumes_storage",
    "app_volumes_database",
    "dem_scope",
    "dem_file_shares",
    "dem_profile_strategy",
    "dr_scenarios",
]


def _question_sort_key(question: Question) -> tuple[int, str]:
    try:
        return (QUESTION_PRIORITY.index(question.key), question.key)
    except ValueError:
        return (999, question.key)


def _is_question_from_product(question: Question, product_key: str) -> bool:
    product = PRODUCTS.get(product_key)
    return bool(product and question in product.follow_up_questions)


def filtered_question_options(question: Question, selected_product_keys: list[str], answers: dict) -> tuple[str, ...]:
    options = tuple(question.options)
    if not options:
        return options

    hosting = _answer(answers, "hosting_strategy")
    site = _answer(answers, "site_topology")
    access = _answer(answers, "access_type")
    load_balancer = _answer(answers, "load_balancer")
    horizon_external_access = _answer(answers, "horizon_external_access")
    mfa_required = _answer(answers, "mfa_required")
    pool_model = _answer(answers, "horizon_pool_model")

    def without(*needles: str) -> tuple[str, ...]:
        return tuple(
            opt for opt in options
            if not any(needle.lower() in opt.lower() for needle in needles)
        )

    if question.key == "hosting_strategy":
        if "horizon_cloud" in selected_product_keys and "horizon_8" not in selected_product_keys:
            return ("Cloud", "Hybrid", UNKNOWN)
        if selected_product_keys == ["unified_access_gateway"]:
            return ("On-premises", "Hybrid", UNKNOWN)
    if question.key == "in_scope" and access == "internal users only":
        return without("external access")
    if question.key == "success_criteria" and site == "single site":
        return without("high availability")
    if question.key == "network_segments" and access == "internal users only":
        return without("dmz")
    if question.key == "firewall_ports":
        if access == "internal users only":
            return without("external", "dmz")
        if access == "external users only":
            return tuple(opt for opt in options if "Internal firewall" not in opt)
    if question.key == "dr_scenarios" and site == "single site":
        return without("site failure")
    if question.key == "open_items" and access == "internal users only":
        return without("fqdn", "certificates", "load balancer vips")
    if question.key == "access_type" and "unified_access_gateway" in selected_product_keys:
        return ("External users only", "Both internal and external", UNKNOWN)
    if question.key == "identity_source":
        if hosting == "on-premises":
            return tuple(opt for opt in options if opt != "Entra ID")
    if question.key == "security_requirements" and mfa_required == "no":
        return tuple(opt for opt in options if opt != "MFA + conditional access")
    if question.key == "availability_requirements":
        if site == "single site":
            return ("N+1 within region", "Single site", UNKNOWN)
        if site == "multi-site":
            return ("Multi-site active/passive", "Multi-site active/active", UNKNOWN)
    if question.key == "horizon_external_access" and access in {"external users only", "both internal and external"}:
        return tuple(opt for opt in options if opt != "No external access")
    if question.key == "horizon_access_topology":
        scoped = options
        if site == "single site":
            scoped = tuple(opt for opt in scoped if "Global" not in opt)
        if "built-in load balancing" in load_balancer or "no load balancer" in load_balancer:
            scoped = tuple(opt for opt in scoped if "load balancer" not in opt.lower() or opt == "Without load balancer")
        elif load_balancer.startswith("yes") or "load balancer" in horizon_external_access:
            scoped = tuple(opt for opt in scoped if opt != "Without load balancer")
        return scoped
    if question.key == "horizon_dmz_design":
        scoped = tuple(opt for opt in options if opt != "No DMZ / internal only")
        if site == "single site":
            scoped = tuple(opt for opt in scoped if "Per-site" not in opt)
        return scoped
    if question.key == "horizon_pod_block_model":
        if site == "single site":
            return tuple(opt for opt in options if "Multi-site" not in opt and "Cloud Pod" not in opt)
        if site == "multi-site":
            return tuple(opt for opt in options if "Single-site" not in opt)
    if question.key == "horizon_8_arch_track":
        if hosting == "on-premises":
            return ("Horizon 8 core architecture", UNKNOWN)
        if hosting == "cloud":
            return tuple(opt for opt in options if opt != "Horizon 8 core architecture")
    if question.key == "horizon_8_design_focus" and access == "internal users only":
        return tuple(opt for opt in options if "UAG" not in opt and "edge" not in opt.lower())
    if question.key == "horizon_cloud_connectivity" and access == "internal users only":
        return tuple(opt for opt in options if "UAG" not in opt)
    if question.key == "access_catalog" and "horizon_8" not in selected_product_keys and "horizon_cloud" not in selected_product_keys:
        return tuple(opt for opt in options if "virtual apps" not in opt and opt != "Unified catalog all apps")
    if question.key == "access_arch_track" and access == "internal users only":
        return tuple(opt for opt in options if "Zero Trust" not in opt)
    if question.key == "uag_edge_pattern" and site == "single site":
        return ("Single DMZ pair", UNKNOWN)
    if question.key == "uag_arch_track" and site == "single site":
        return tuple(opt for opt in options if "global" not in opt.lower() and "load-balanced" not in opt.lower())
    if question.key == "uag_design_focus" and ("built-in load balancing" in load_balancer or "no load balancer" in load_balancer or site == "single site"):
        return tuple(opt for opt in options if "scale-out" not in opt)
    return options


def essential_question_keys(selected_product_keys: list[str], answers: dict) -> set[str]:
    """Public wrapper: keys of the essential (must-ask) questions."""
    return _essential_question_keys(selected_product_keys, answers)


def should_show_question(question: Question, selected_product_keys: list[str], answers: dict, include_optional: bool = False) -> bool:
    hosting = _answer(answers, "hosting_strategy")
    access = _answer(answers, "access_type")
    mfa_required = _answer(answers, "mfa_required")
    site = _answer(answers, "site_topology")
    load_balancer = _answer(answers, "load_balancer")
    horizon_external_access = _answer(answers, "horizon_external_access")
    pool_model = _answer(answers, "horizon_pool_model")

    if not include_optional and question.key not in _essential_question_keys(selected_product_keys, answers):
        return False
    if question.show_if and not any(normalize_answer(answers.get(k)) == v for k, v in question.show_if):
        return False
    if question.show_if_all and not all(normalize_answer(answers.get(k)) == v for k, v in question.show_if_all):
        return False
    if _is_question_from_product(question, "horizon_cloud") and hosting == "on-premises":
        return False
    if _is_question_from_product(question, "unified_access_gateway") and access == "internal users only":
        return False
    if question.key == "mfa_provider" and mfa_required == "no":
        return False
    if question.key == "desktop_image_version" and "desktop" not in pool_model:
        return False
    if question.key == "server_image_version" and "server" not in pool_model:
        return False
    if question.key in {"load_balancer_name", "load_balancer_placement"} and not load_balancer.startswith("yes"):
        return False
    if question.key in {"load_balancer", "fqdn_strategy", "cert_type", "certificate_owner"} and not _is_external_access(answers):
        return False
    if question.key == "horizon_access_topology" and "load balancer" in horizon_external_access:
        return False
    if question.key in {"horizon_external_access", "horizon_access_topology", "horizon_dmz_design"} and not _is_external_access(answers):
        return False
    if question.key in {"uag_nic_config", "uag_services", "uag_edge_pattern", "uag_arch_track", "uag_design_focus"} and not _is_external_access(answers):
        return False
    if question.key == "secondary_sites" and not _is_multi_site(answers):
        return False
    if question.key in {"dr_scenarios"} and site == "single site" and _answer(answers, "availability_requirements") in {"single site", "n+1 within region"}:
        return False
    if question.key == "horizon_cloud_provider" and hosting == "on-premises":
        return False
    if question.key == "horizon_protocol_scope":
        return False
    if not include_optional and question.key in {"network_segments", "horizon_connection_server_count"}:
        return False

    filtered_options = filtered_question_options(question, selected_product_keys, answers)
    inferred_single_choice_questions = {"horizon_access_topology", "horizon_dmz_design", "uag_edge_pattern", "uag_arch_track"}
    if question.key in inferred_single_choice_questions and len(filtered_options) == 1:
        return False
    return bool(filtered_options or not question.options)


def visible_questions(selected_product_keys: list[str], answers: dict, include_optional: bool = False) -> list[Question]:
    base = list(required_questions(selected_product_keys))
    has_horizon_broker = any(k in selected_product_keys for k in ("horizon_8", "horizon_cloud"))
    uag_not_selected = "unified_access_gateway" not in selected_product_keys
    access = _answer(answers, "access_type")
    if has_horizon_broker and uag_not_selected and ("external" in access or "both" in access):
        existing_keys = {qst.key for qst in base}
        for qst in UAG.follow_up_questions:
            if qst.key == "uag_nic_config" and qst.key not in existing_keys:
                base.append(qst)
    return sorted(
        [question for question in base if should_show_question(question, selected_product_keys, answers, include_optional=include_optional)],
        key=_question_sort_key,
    )


QUESTION_SOURCE_HINTS: dict[str, Resource] = {
    "business_drivers": Resource("Business Drivers, Use Cases and Service Definitions", "https://techzone.omnissa.com/resource/business-drivers-use-cases-and-service-definitions", "business drivers use cases service definitions requirements"),
    "success_criteria": Resource("Workspace ONE and Horizon Reference Architecture", "https://techzone.omnissa.com/resource/workspace-one-and-horizon-reference-architecture-overview", "outcome validation success criteria requirements"),
    "hosting_strategy": Resource("Workspace ONE and Horizon Reference Architecture", "https://techzone.omnissa.com/resource/workspace-one-and-horizon-reference-architecture-overview", "deployment model hosting on-premises cloud hybrid architecture"),
    "site_topology": Resource("Horizon 8 Architecture", "https://techzone.omnissa.com/resource/horizon-8-architecture", "single-site multi-site pods availability"),
    "access_type": Resource("Understand and Troubleshoot Horizon Connections", "https://techzone.omnissa.com/resource/understand-and-troubleshoot-horizon-connections", "internal external client connection flow"),
    "load_balancer": Resource("Horizon 8 Architecture", "https://techzone.omnissa.com/resource/horizon-8-architecture", "load balanced connection servers horizon clients"),
    "load_balancer_name": Resource("Load Balancing Unified Access Gateway for Horizon", "https://techzone.omnissa.com/resource/load-balancing-unified-access-gateway-horizon", "load balancer platform vip uag connection server"),
    "load_balancer_placement": Resource("Load Balancing Unified Access Gateway for Horizon", "https://techzone.omnissa.com/resource/load-balancing-unified-access-gateway-horizon", "load balancer placement uag connection server"),
    "fqdn_strategy": Resource("Unified Access Gateway Architecture", "https://techzone.omnissa.com/resource/unified-access-gateway-architecture", "fqdn dns split dns external internal url"),
    "cert_type": Resource("Unified Access Gateway Architecture", "https://techzone.omnissa.com/resource/unified-access-gateway-architecture", "ssl tls certificate private key"),
    "identity_source": Resource("Horizon 8 Architecture", "https://techzone.omnissa.com/resource/horizon-8-architecture", "active directory identity authentication"),
    "firewall_ports": Resource("Network Ports in Horizon 8", "https://techzone.omnissa.com/resource/network-ports-horizon-8", "network ports firewall blast pcoip"),
    "security_requirements": Resource("Workspace ONE and Horizon Reference Architecture", "https://techzone.omnissa.com/resource/workspace-one-and-horizon-reference-architecture-overview", "security controls compliance mfa"),
    "mfa_required": Resource("Horizon 8 Architecture", "https://techzone.omnissa.com/resource/horizon-8-architecture", "multi-factor authentication saml radius"),
    "availability_requirements": Resource("Horizon 8 Architecture", "https://techzone.omnissa.com/resource/horizon-8-architecture", "availability n+1 multi-site active passive active active"),
    "backup_requirements": Resource("Horizon 8 Configuration", "https://techzone.omnissa.com/resource/horizon-8-configuration", "backup recovery configuration golden image"),
    "horizon_use_cases": Resource("Business Drivers, Use Cases and Service Definitions", "https://techzone.omnissa.com/resource/business-drivers-use-cases-and-service-definitions", "horizon service definitions pooled persistent rdsh apps"),
    "horizon_pool_model": Resource("Horizon 8 Configuration", "https://techzone.omnissa.com/resource/horizon-8-configuration", "desktop pools farms instant clone rdsh"),
    "desktop_image_version": Resource("Horizon 8 Configuration", "https://techzone.omnissa.com/resource/horizon-8-configuration", "desktop image operating system version"),
    "server_image_version": Resource("Horizon 8 Configuration", "https://techzone.omnissa.com/resource/horizon-8-configuration", "rdsh server image operating system version"),
    "horizon_pod_block_model": Resource("Horizon 8 Architecture", "https://techzone.omnissa.com/resource/horizon-8-architecture", "pod block cloud pod architecture"),
    "horizon_connection_server_count": Resource("Reference Architecture VM Specifications", "https://techzone.omnissa.com/resource/reference-architecture-vm-specifications", "connection server vm specifications sizing"),
    "horizon_external_access": Resource("Unified Access Gateway Architecture", "https://techzone.omnissa.com/resource/unified-access-gateway-architecture", "external access horizon uag dmz"),
    "horizon_access_topology": Resource("Unified Access Gateway Architecture", "https://techzone.omnissa.com/resource/unified-access-gateway-architecture", "load balanced edge topology"),
    "horizon_dmz_design": Resource("Unified Access Gateway Architecture", "https://techzone.omnissa.com/resource/unified-access-gateway-architecture", "single dmz double dmz network segmentation"),
    "horizon_8_arch_track": Resource("Horizon 8 Architecture", "https://techzone.omnissa.com/resource/horizon-8-architecture", "core cloud platform architecture"),
    "horizon_8_design_focus": Resource("Horizon 8 Architecture", "https://techzone.omnissa.com/resource/horizon-8-architecture", "component design workloads network availability"),
    "horizon_database_events": Resource("Horizon 8 Configuration", "https://techzone.omnissa.com/resource/horizon-8-configuration", "events database sql monitoring"),
    "horizon_golden_image": Resource("Horizon 8 Configuration", "https://techzone.omnissa.com/resource/horizon-8-configuration", "golden image parent vm optimization"),
    "app_volumes_arch_track": Resource("App Volumes Architecture", "https://techzone.omnissa.com/resource/app-volumes-architecture", "app volumes architecture"),
    "app_volumes_storage": Resource("App Volumes Architecture", "https://techzone.omnissa.com/resource/app-volumes-architecture", "storage groups datastore packages"),
    "app_volumes_database": Resource("App Volumes Architecture", "https://techzone.omnissa.com/resource/app-volumes-architecture", "database sql availability"),
    "dem_arch_track": Resource("Dynamic Environment Manager Architecture", "https://techzone.omnissa.com/resource/dynamic-environment-manager-architecture", "dem architecture"),
    "dem_file_shares": Resource("Dynamic Environment Manager Architecture", "https://techzone.omnissa.com/resource/dynamic-environment-manager-architecture", "configuration share profile archive smb"),
    "dem_profile_strategy": Resource("Dynamic Environment Manager Architecture", "https://techzone.omnissa.com/resource/dynamic-environment-manager-architecture", "profile strategy fslogix"),
    "uag_nic_config": Resource("Deploying Unified Access Gateway", "https://techzone.omnissa.com/resource/deploying-unified-access-gateway", "uag nic configuration"),
    "uag_arch_track": Resource("Unified Access Gateway Architecture", "https://techzone.omnissa.com/resource/unified-access-gateway-architecture", "uag high availability load balanced edge"),
}


def question_source(question: Question, selected_product_keys: list[str]) -> Resource | None:
    hint = QUESTION_SOURCE_HINTS.get(question.key)
    if hint:
        return hint
    if question.source_url and is_allowed_techzone_url(question.source_url):
        return Resource(
            title=question.source_title or "Tech Zone source",
            url=question.source_url,
            summary=question.source_query or question.source_section_title or "Question source",
        )
    return None


def effective_answers(answers: dict[str, str]) -> dict[str, str]:
    resolved = dict(answers)
    load_balancer = _answer(resolved, "load_balancer")
    external_access = _answer(resolved, "horizon_external_access")
    if "load balancer" in external_access and not resolved.get("horizon_access_topology"):
        resolved["horizon_access_topology"] = "With load balancer"
    if load_balancer == "built-in load balancing":
        resolved.setdefault("load_balancer_name", "Built-in load balancing")
        resolved.setdefault("load_balancer_placement", "Built-in service load balancing")
    resolved.setdefault("horizon_protocol_scope", "Blast Extreme only")
    resolved.setdefault("network_segments", _infer_network_segments(resolved))
    resolved.setdefault("horizon_connection_server_count", _infer_connection_server_count(resolved))
    return resolved


def _infer_network_segments(answers: dict[str, str]) -> str:
    access = _answer(answers, "access_type")
    workloads = _answer(answers, "horizon_use_cases")
    segments = ["Management", "VDI workloads", "User/endpoint networks"]
    image_model = _answer(answers, "horizon_pool_model")
    if "published" in workloads or "rdsh" in image_model or "server" in image_model:
        segments.append("RDSH workloads")
    if access in {"external users only", "both internal and external"}:
        segments.append("DMZ")
    return "; ".join(dict.fromkeys(segments))


def _infer_connection_server_count(answers: dict[str, str]) -> str:
    concurrency = str(answers.get("workload_concurrency", "") or "").lower()
    if "2,000+" in concurrency or "2000+" in concurrency:
        return "3-4 per pod/site"
    numbers = [int(item.replace(",", "")) for item in re.findall(r"\d[\d,]*", concurrency)]
    if numbers and max(numbers) > 2000:
        return "3-4 per pod/site"
    if numbers:
        return "2 per pod/site"
    return "2 per pod/site, to be validated against final concurrency"


def compile_reference_resources(selected_product_keys: list[str]) -> list[Resource]:
    resources: list[Resource] = []
    seen: set[str] = set()
    for key in selected_product_keys:
        product = PRODUCTS.get(key)
        if not product:
            continue
        for resource in (product.resource, *product.related_resources):
            if resource.url in seen:
                continue
            if is_allowed_techzone_url(resource.url):
                resources.append(resource)
                seen.add(resource.url)
    return resources
