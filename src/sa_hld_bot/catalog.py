from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


TECHZONE_DOMAIN = "techzone.omnissa.com"
TECHZONE_SITEMAP_URL = f"https://{TECHZONE_DOMAIN}/sitemap.xml"


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    help_text: str = ""
    options: tuple[str, ...] = ()
    allow_custom: bool = True


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


COMMON_QUESTIONS: tuple[Question, ...] = (
    Question(
        "customer_name",
        "What is the customer name for this HLD?",
        help_text="Use the legal/entity name you want on the title slide.",
        options=(),
        allow_custom=True,
    ),
    Question(
        "industry",
        "Which industry best matches the customer?",
        help_text="Pick the closest business domain to tailor architecture language.",
        options=("Healthcare", "Financial Services", "Manufacturing", "Public Sector", "Retail", "Technology"),
    ),
    Question(
        "project_scope",
        "What is the primary business objective for this initiative?",
        help_text="Focus on the most important outcome this design must deliver.",
        options=("Modernize EUC platform", "Enable secure remote work", "Improve user experience", "Reduce operational cost", "Strengthen security posture"),
    ),
    Question(
        "users_personas",
        "Which user personas are in scope?",
        help_text="Choose the dominant end-user profile for sizing and workload patterns.",
        options=("Task workers", "Knowledge workers", "Developers", "Clinicians", "Contact center users", "Mixed workforce"),
    ),
    Question(
        "hosting_strategy",
        "What hosting strategy should this design target?",
        help_text="Select the intended landing zone for management and workloads.",
        options=("On-premises", "Cloud", "Hybrid"),
    ),
    Question(
        "identity_source",
        "What is the identity source for authentication and access?",
        help_text="Pick the user directory / identity provider used by the customer.",
        options=("Active Directory", "Entra ID", "Hybrid AD + Entra ID", "LDAP"),
    ),
    Question(
        "network_constraints",
        "What network posture should we design for?",
        help_text="Choose the closest access pattern and network constraint profile.",
        options=("Internal only", "Internal + external users", "Zero Trust internet-first", "Branch office constrained bandwidth"),
    ),
    Question(
        "security_requirements",
        "Which security baseline is required?",
        help_text="Select the control profile that best fits compliance and risk expectations.",
        options=("MFA + conditional access", "Compliance-first (audit heavy)", "Data loss prevention focus", "High security segmentation"),
    ),
    Question(
        "availability_requirements",
        "What availability target should the architecture meet?",
        help_text="Choose the resiliency expectation for management and workload tiers.",
        options=("Single site", "N+1 within region", "Multi-site active/passive", "Multi-site active/active"),
    ),
    Question(
        "timeline",
        "What is the expected implementation timeline?",
        help_text="Use the realistic delivery window to guide phased recommendations.",
        options=("0-3 months", "3-6 months", "6-12 months", "12+ months"),
    ),
    Question(
        "assumptions",
        "Any dependencies, assumptions, or constraints we must capture?",
        help_text="Include known blockers, prerequisites, or external teams/services.",
        options=(),
        allow_custom=True,
    ),
)


HORIZON_8 = Product(
    key="horizon_8",
    title="Horizon 8",
    family="Horizon",
    summary="Customer-managed Horizon deployment for VDI and published applications.",
    resource=Resource(
        title="What Is Omnissa Horizon?",
        url="https://techzone.omnissa.com/resource/what-omnissa-horizon",
        summary="Horizon product family introduction and selection guidance.",
    ),
    related_resources=(
        Resource(
            title="Horizon 8 Architecture",
            url="https://techzone.omnissa.com/resource/horizon-8-architecture",
            summary="Core Horizon 8 architecture design guidance.",
        ),
        Resource(
            title="Horizon 8 on VMware Cloud on AWS Architecture",
            url="https://techzone.omnissa.com/resource/horizon-8-vmware-cloud-aws-architecture",
            summary="Reference architecture for Horizon 8 on VMware Cloud on AWS.",
        ),
        Resource(
            title="Horizon 8 on Azure VMware Solution Architecture",
            url="https://techzone.omnissa.com/resource/horizon-8-azure-vmware-solution-architecture",
            summary="Reference architecture for Horizon 8 on AVS.",
        ),
        Resource(
            title="Horizon 8 on Google Cloud VMware Engine Architecture",
            url="https://techzone.omnissa.com/resource/horizon-8-google-cloud-vmware-engine-architecture",
            summary="Reference architecture for Horizon 8 on GCVE.",
        ),
        Resource(
            title="Horizon 8 on Oracle Cloud VMware Solution Architecture",
            url="https://techzone.omnissa.com/resource/horizon-8-oracle-cloud-vmware-solution-architecture",
            summary="Reference architecture for Horizon 8 on OCVS.",
        ),
        Resource(
            title="Horizon 8 on Alibaba Cloud VMware Service Architecture",
            url="https://techzone.omnissa.com/resource/horizon-8-alibaba-cloud-vmware-service-architecture",
            summary="Reference architecture for Horizon 8 on ACVS.",
        ),
        Resource(
            title="Unified Access Gateway Architecture",
            url="https://techzone.omnissa.com/resource/unified-access-gateway-architecture",
            summary="Secure remote access architecture patterns for Horizon and Workspace ONE.",
        ),
    ),
    follow_up_questions=(
        Question(
            "horizon_use_cases",
            "Which Horizon 8 workloads are required?",
            help_text="Select the primary desktop/app delivery pattern.",
            options=("Pooled VDI", "Persistent VDI", "Published apps", "Mixed VDI + published apps"),
        ),
        Question(
            "horizon_capacity",
            "What is the expected peak concurrent user load?",
            help_text="Choose your best estimate for concurrent sessions.",
            options=("Up to 500", "500-2,000", "2,000-10,000", "10,000+"),
        ),
        Question(
            "horizon_image_strategy",
            "Which image/application delivery strategy should be used?",
            help_text="Pick the preferred desktop image and app lifecycle model.",
            options=("Instant Clone + golden image", "Persistent desktops", "App layering with App Volumes"),
        ),
        Question(
            "horizon_external_access",
            "How should external access be handled?",
            help_text="Select the edge access model for remote users.",
            options=("No external access", "UAG in DMZ", "UAG + load balancer", "Zero Trust edge"),
        ),
        Question(
            "horizon_access_topology",
            "Which remote access topology best fits the customer?",
            help_text="This helps choose the right external access diagrams for the deck.",
            options=("Without load balancer", "With load balancer", "Global load-balanced edge"),
        ),
        Question(
            "horizon_dmz_design",
            "Which DMZ design should the client presentation cover?",
            help_text="Choose the edge security layout the customer is most likely to adopt.",
            options=("Single DMZ", "Double DMZ", "Per-site DMZ pair", "No DMZ / internal only"),
        ),
        Question(
            "horizon_protocol_scope",
            "Which display protocols should the network section cover?",
            help_text="Pick only the protocols that are relevant so the PPT stays focused.",
            options=("Blast Extreme only", "Blast + PCoIP", "Blast + PCoIP + RDP"),
        ),
        Question(
            "horizon_8_arch_track",
            "Which Horizon 8 architecture path best matches your target platform?",
            help_text="Pick the exact architecture track so the deck can align to the right Tech Zone reference diagrams.",
            options=(
                "Horizon 8 core architecture",
                "Horizon 8 on VMware Cloud on AWS",
                "Horizon 8 on Azure VMware Solution",
                "Horizon 8 on Google Cloud VMware Engine",
                "Horizon 8 on Oracle Cloud VMware Solution",
                "Horizon 8 on Alibaba Cloud VMware Service",
            ),
        ),
        Question(
            "horizon_8_design_focus",
            "Which Horizon 8 design section should be prioritized?",
            help_text="Select the section where you want the most detailed architecture decisions in the HLD.",
            options=(
                "Management components and control plane",
                "Desktop/RDS host workload pools",
                "Network, UAG, and edge access",
                "Availability, scale, and DR",
            ),
        ),
    ),
)

HORIZON_CLOUD = Product(
    key="horizon_cloud",
    title="Horizon Cloud",
    family="Horizon",
    summary="Cloud-delivered desktop and app service with Horizon control plane.",
    resource=Resource(
        title="What Is Omnissa Horizon?",
        url="https://techzone.omnissa.com/resource/what-omnissa-horizon",
        summary="Overview that positions Horizon Cloud and Horizon 8.",
    ),
    related_resources=(
        Resource(
            title="Horizon Cloud Service Next-Gen Architecture",
            url="https://techzone.omnissa.com/resource/horizon-cloud-service-next-gen-architecture",
            summary="Reference architecture for Horizon Cloud Service next-gen.",
        ),
        Resource(
            title="Horizon Cloud Service Next-Gen Configuration",
            url="https://techzone.omnissa.com/resource/horizon-cloud-service-next-gen-configuration",
            summary="Configuration patterns for Horizon Cloud Service next-gen.",
        ),
        Resource(
            title="Horizon Cloud Service Security Overview",
            url="https://techzone.omnissa.com/resource/horizon-cloud-service-security-overview",
            summary="Security and responsibility model for Horizon Cloud service.",
        ),
    ),
    follow_up_questions=(
        Question(
            "horizon_cloud_provider",
            "Which cloud platform will host Horizon Cloud workloads?",
            help_text="Choose the primary hyperscaler target for deployment.",
            options=("Microsoft Azure", "AWS", "Google Cloud"),
        ),
        Question(
            "horizon_cloud_use_cases",
            "What is the main Horizon Cloud adoption scenario?",
            help_text="Pick the primary reason for using Horizon Cloud.",
            options=("DaaS migration", "Cloud burst", "Net new desktop service", "Hybrid control plane"),
        ),
        Question(
            "horizon_cloud_connectivity",
            "Which connectivity architecture is preferred?",
            help_text="Select the expected network interconnect model to cloud.",
            options=("Private connectivity", "Internet + UAG", "Hub-and-spoke network"),
        ),
        Question(
            "horizon_cloud_arch_track",
            "Which Horizon Cloud architecture track should we follow?",
            help_text="Choose the cloud architecture baseline for solution decisions.",
            options=(
                "Horizon Cloud next-gen architecture",
                "Horizon Cloud security architecture focus",
                "Horizon Cloud connectivity-first architecture",
            ),
        ),
        Question(
            "horizon_cloud_design_focus",
            "Which Horizon Cloud section needs the deepest design detail?",
            help_text="Pick the section where you want stronger solution depth in the HLD.",
            options=(
                "Pod/control plane and tenant model",
                "Desktop/app workload delivery",
                "Identity and access integration",
                "Operations, monitoring, and resilience",
            ),
        ),
    ),
)

APP_VOLUMES = Product(
    key="app_volumes",
    title="App Volumes",
    family="Horizon",
    summary="Application packaging and dynamic delivery for VDI/app sessions.",
    resource=Resource(
        title="What Is Omnissa App Volumes?",
        url="https://techzone.omnissa.com/resource/what-omnissa-app-volumes",
        summary="App Volumes overview and design considerations.",
    ),
    related_resources=(
        Resource(
            title="App Volumes Architecture",
            url="https://techzone.omnissa.com/resource/app-volumes-architecture",
            summary="Reference architecture for App Volumes.",
        ),
    ),
    follow_up_questions=(
        Question(
            "app_volumes_scope",
            "How do you plan to use App Volumes?",
            help_text="Select the dominant app delivery/lifecycle use case.",
            options=("App attach for non-persistent VDI", "Dynamic app entitlements", "Lifecycle simplification"),
        ),
        Question(
            "app_volumes_arch_track",
            "Which App Volumes architecture path best fits this project?",
            help_text="Choose the architecture/design baseline for packaging and delivery.",
            options=(
                "App Volumes architecture baseline",
                "App Volumes packaging and lifecycle operations",
                "App Volumes with Horizon published apps on-demand",
            ),
        ),
        Question(
            "app_volumes_design_focus",
            "Which App Volumes section should be prioritized?",
            help_text="Pick the area where detailed design guidance is most needed.",
            options=(
                "Application packaging and capture",
                "Assignment and entitlement model",
                "Storage/performance and scale",
                "Operational governance and lifecycle",
            ),
        ),
    ),
)

DEM = Product(
    key="dynamic_environment_manager",
    title="Dynamic Environment Manager",
    family="Horizon",
    summary="Context-aware user profile and environment policy management.",
    resource=Resource(
        title="What Is Omnissa Horizon?",
        url="https://techzone.omnissa.com/resource/what-omnissa-horizon",
        summary="Horizon portfolio context including DEM.",
    ),
    related_resources=(
        Resource(
            title="Dynamic Environment Manager Architecture",
            url="https://techzone.omnissa.com/resource/dynamic-environment-manager-architecture",
            summary="Reference architecture for Dynamic Environment Manager.",
        ),
    ),
    follow_up_questions=(
        Question(
            "dem_scope",
            "What should DEM primarily manage?",
            help_text="Pick the most important user environment policy scope.",
            options=("Profile management", "Context-based policies", "Printer and drive mapping", "All of the above"),
        ),
        Question(
            "dem_arch_track",
            "Which DEM architecture/design path should we use?",
            help_text="Choose the best-fit track for user environment management decisions.",
            options=(
                "DEM architecture baseline",
                "DEM configuration and policy operations",
                "DEM with app personalization focus",
            ),
        ),
        Question(
            "dem_design_focus",
            "Which DEM section should be designed in detail?",
            help_text="Pick the area requiring deepest implementation planning.",
            options=(
                "Profile archives and configuration shares",
                "Context-based conditions and policies",
                "User personalization and app settings",
                "Operations and troubleshooting",
            ),
        ),
    ),
)

WORKSPACE_ONE_UEM = Product(
    key="workspace_one_uem",
    title="Workspace ONE UEM",
    family="Workspace ONE",
    summary="Unified endpoint management across modern device platforms.",
    resource=Resource(
        title="What Is Workspace ONE?",
        url="https://techzone.omnissa.com/resource/what-workspace-one",
        summary="Workspace ONE platform and UEM overview.",
    ),
    related_resources=(
        Resource(
            title="Workspace ONE UEM Architecture",
            url="https://techzone.omnissa.com/resource/workspace-one-uem-architecture",
            summary="Reference architecture for Workspace ONE UEM.",
        ),
    ),
    follow_up_questions=(
        Question(
            "uem_platforms",
            "Which device platforms are in scope for UEM?",
            help_text="Choose the platform mix you need to manage.",
            options=("Windows + macOS", "iOS + Android", "All major platforms", "Rugged/shared devices"),
        ),
        Question(
            "uem_ownership",
            "What is the device ownership model?",
            help_text="Select the endpoint ownership pattern used by the customer.",
            options=("Corporate-owned", "BYOD", "COPE", "Mixed"),
        ),
        Question(
            "uem_security",
            "Which UEM security control theme matters most?",
            help_text="Choose the strongest management/security priority for endpoints.",
            options=("Compliance + conditional launch", "Patch and vulnerability baseline", "Application control"),
        ),
        Question(
            "uem_arch_track",
            "Which Workspace ONE UEM architecture path should guide this HLD?",
            help_text="Choose the architecture/design baseline to align solution recommendations.",
            options=(
                "Workspace ONE UEM architecture baseline",
                "Workspace ONE UEM modern SaaS architecture",
                "Workspace ONE UEM configuration-first path",
            ),
        ),
        Question(
            "uem_design_focus",
            "Which UEM design section should be prioritized?",
            help_text="Pick the section that needs the strongest technical depth.",
            options=(
                "Enrollment and onboarding workflows",
                "Profiles, compliance, and conditional controls",
                "Application lifecycle and software distribution",
                "Operations, reporting, and governance",
            ),
        ),
    ),
)

OMNISSA_ACCESS = Product(
    key="omnissa_access",
    title="Omnissa Access",
    family="Workspace ONE",
    summary="Identity, SSO, conditional access, and app catalog services.",
    resource=Resource(
        title="What Is Workspace ONE?",
        url="https://techzone.omnissa.com/resource/what-workspace-one",
        summary="Workspace ONE identity and access context.",
    ),
    related_resources=(
        Resource(
            title="Workspace ONE Access Architecture",
            url="https://techzone.omnissa.com/resource/workspace-one-access-architecture",
            summary="Reference architecture for Workspace ONE Access.",
        ),
        Resource(
            title="Workspace ONE Access Configuration",
            url="https://techzone.omnissa.com/resource/workspace-one-access-configuration",
            summary="Configuration reference for Workspace ONE Access.",
        ),
    ),
    follow_up_questions=(
        Question(
            "access_authentication",
            "What authentication baseline should Access enforce?",
            help_text="Pick the preferred sign-in strength and user experience.",
            options=("Password + MFA", "Passwordless + MFA", "Certificate-based auth", "Adaptive auth"),
        ),
        Question(
            "access_catalog",
            "How broad should the app catalog be?",
            help_text="Select which app classes should be unified in the user catalog.",
            options=("SaaS only", "SaaS + virtual apps", "SaaS + on-prem web apps", "Unified catalog all apps"),
        ),
        Question(
            "access_arch_track",
            "Which Access architecture path should this solution follow?",
            help_text="Choose the identity architecture baseline for the HLD.",
            options=(
                "Workspace ONE Access architecture baseline",
                "Workspace ONE Access configuration-first path",
                "Access with Zero Trust conditional access focus",
            ),
        ),
        Question(
            "access_design_focus",
            "Which Access section should be designed in detail?",
            help_text="Pick the area where deeper identity/access architecture is required.",
            options=(
                "Authentication and MFA patterns",
                "Directory sync and identity source integration",
                "Application catalog and federation",
                "Access policy, risk, and compliance controls",
            ),
        ),
    ),
)

UAG = Product(
    key="unified_access_gateway",
    title="Unified Access Gateway",
    family="Workspace ONE",
    summary="Secure edge gateway for remote access to Horizon and Workspace ONE services.",
    resource=Resource(
        title="Unified Access Gateway Architecture",
        url="https://techzone.omnissa.com/resource/unified-access-gateway-architecture",
        summary="Reference architecture for DMZ edge and remote access.",
    ),
    related_resources=(
        Resource(
            title="Deploying Unified Access Gateway",
            url="https://techzone.omnissa.com/resource/deploying-unified-access-gateway",
            summary="Deployment guidance for Unified Access Gateway.",
        ),
        Resource(
            title="Configuring High Availability for Unified Access Gateway",
            url="https://techzone.omnissa.com/resource/configuring-high-availability-unified-access-gateway",
            summary="High-availability architecture guidance for UAG.",
        ),
        Resource(
            title="Load Balancing UAG for Horizon",
            url="https://techzone.omnissa.com/resource/load-balancing-unified-access-gateway-horizon",
            summary="Load balancing and scale design patterns for UAG.",
        ),
    ),
    follow_up_questions=(
        Question(
            "uag_services",
            "Which services should Unified Access Gateway publish?",
            help_text="Choose the edge service mix required for this customer.",
            options=("Horizon edge", "Tunnel and web reverse proxy", "Content services edge", "Combined services"),
        ),
        Question(
            "uag_edge_pattern",
            "What edge deployment pattern should be used?",
            help_text="Select the scale/HA pattern for UAG placement.",
            options=("Single DMZ pair", "Per-site DMZ pair", "Global load-balanced edge"),
        ),
        Question(
            "uag_arch_track",
            "Which UAG architecture track should guide the design?",
            help_text="Choose the UAG architecture/deployment baseline to shape the HLD.",
            options=(
                "UAG architecture baseline",
                "UAG high-availability architecture",
                "UAG load-balanced global edge architecture",
            ),
        ),
        Question(
            "uag_design_focus",
            "Which UAG design section should be prioritized?",
            help_text="Pick the edge topic that needs detailed design decisions.",
            options=(
                "DMZ placement and network segmentation",
                "Protocol/service edge configuration",
                "High availability and scale-out topology",
                "Certificate, security hardening, and operations",
            ),
        ),
    ),
)


PRODUCTS: dict[str, Product] = {
    item.key: item
    for item in (
        HORIZON_8,
        HORIZON_CLOUD,
        APP_VOLUMES,
        DEM,
        WORKSPACE_ONE_UEM,
        OMNISSA_ACCESS,
        UAG,
    )
}

FAMILY_CHOICES: dict[str, tuple[str, ...]] = {
    "Horizon": ("horizon_8", "horizon_cloud", "app_volumes", "dynamic_environment_manager"),
    "Workspace ONE": ("workspace_one_uem", "omnissa_access", "unified_access_gateway"),
}


def required_questions(selected_product_keys: list[str]) -> list[Question]:
    questions: list[Question] = list(COMMON_QUESTIONS)
    for key in selected_product_keys:
        product = PRODUCTS.get(key)
        if product:
            questions.extend(product.follow_up_questions)
    return questions


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


def normalize_answer(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()
