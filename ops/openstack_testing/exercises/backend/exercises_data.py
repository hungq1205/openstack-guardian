"""All exercise definitions: the task text shown to the user, the one-time
warning, hint/solution, and the init/check/answer logic that actually talks
to the real cluster. Mechanism/fix text is drawn from the same source as the
Error Reproduction tab in ../progress.html -- kept independent here since
this app grades live state, not just documents the scenario.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import requests

from . import config, ssh_exec
from .os_client import delete_instance_if_exists, find_instance, find_unique_image


@dataclass
class Question:
    prompt: str
    checker: Callable[[object, str], tuple[bool, str]]  # (conn, answer) -> (correct, message)


@dataclass
class CommandRef:
    name: str
    description: str
    options: list[tuple[str, str]]  # (flag/arg, what it means)


@dataclass
class Exercise:
    id: str
    title: str
    tier: int
    risk: str  # "none" | "low" | "medium"
    goal: str
    task: str
    commands: list[CommandRef]
    warning: str
    hint: str
    solution: str
    init_fn: Callable[[object], tuple[bool, str]]
    check_fn: Callable[[object], tuple[bool, str]] | None = None
    question: Question | None = None


def _ensure_baseline(conn) -> list[str]:
    messages = []

    flavor = conn.compute.find_flavor("tiny")
    if flavor is None:
        conn.compute.create_flavor(name="tiny", vcpus=1, ram=512, disk=1)
        messages.append("created flavor 'tiny' (1 vCPU / 512MB / 1GB)")
    else:
        messages.append("flavor 'tiny' already exists")

    network = conn.network.find_network("ex-net", project_id=conn.current_project_id)
    if network is None:
        network = conn.network.create_network(name="ex-net")
        conn.network.create_subnet(
            network_id=network.id, name="ex-subnet", ip_version=4,
            cidr="10.10.10.0/24",
            allocation_pools=[{"start": "10.10.10.10", "end": "10.10.10.250"}],
        )
        messages.append("created network 'ex-net' + subnet 'ex-subnet' (10.10.10.0/24)")
    else:
        messages.append("network 'ex-net' already exists")

    image = find_unique_image(conn, "cirros")
    if image is None:
        if not config.CIRROS_PATH.exists():
            config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
            resp = requests.get(config.CIRROS_URL, timeout=90)
            resp.raise_for_status()
            config.CIRROS_PATH.write_bytes(resp.content)
            messages.append("downloaded the cirros test image")
        conn.image.create_image(
            name="cirros", filename=str(config.CIRROS_PATH),
            disk_format="qcow2", container_format="bare",
        )
        messages.append("uploaded image 'cirros' to glance")
    else:
        messages.append("image 'cirros' already exists")

    keypair = conn.compute.find_keypair(config.EXERCISE_KEYPAIR_NAME)
    if keypair is None:
        pubkey = Path(config.EXERCISE_PUBKEY_PATH).read_text().strip()
        conn.compute.create_keypair(name=config.EXERCISE_KEYPAIR_NAME, public_key=pubkey)
        messages.append(f"imported keypair '{config.EXERCISE_KEYPAIR_NAME}'")
    else:
        messages.append(f"keypair '{config.EXERCISE_KEYPAIR_NAME}' already exists")

    default_sg = conn.network.find_security_group("default", project_id=conn.current_project_id)
    if default_sg is not None:
        rules = list(conn.network.security_group_rules(security_group_id=default_sg.id))
        has_ssh = any(r.protocol == "tcp" and r.port_range_min == 22 for r in rules)
        has_icmp = any(r.protocol == "icmp" for r in rules)
        if not has_ssh:
            conn.network.create_security_group_rule(
                security_group_id=default_sg.id, direction="ingress",
                protocol="tcp", port_range_min=22, port_range_max=22, ethertype="IPv4",
            )
            messages.append("added an SSH ingress rule to the 'default' security group")
        if not has_icmp:
            conn.network.create_security_group_rule(
                security_group_id=default_sg.id, direction="ingress",
                protocol="icmp", ethertype="IPv4",
            )
            messages.append("added an ICMP ingress rule to the 'default' security group")
    return messages


def _server_active(conn, name: str):
    server = find_instance(conn, name)
    if server is None:
        return None
    return conn.compute.get_server(server.id)


# ---------------------------------------------------------------- exercise 0

def init_ex0(conn):
    messages = _ensure_baseline(conn)
    delete_instance_if_exists(conn, "ex0-vm")
    return True, "; ".join(messages) + "; cleared any leftover ex0-vm"


def check_ex0(conn):
    server = _server_active(conn, "ex0-vm")
    if server is None:
        return False, "no instance named 'ex0-vm' found yet"
    if server.status == "ACTIVE":
        return True, f"ex0-vm is ACTIVE (task_state={server.task_state})"
    return False, f"ex0-vm status={server.status} task_state={server.task_state} -- not there yet"


def _question_ex0(conn, answer: str):
    flavor = conn.compute.find_flavor("tiny")
    if flavor is None:
        return False, "flavor 'tiny' doesn't exist yet -- run Init first"
    try:
        given = int(answer.strip())
    except ValueError:
        return False, "answer should be a number (vCPU count)"
    return given == flavor.vcpus, f"flavor 'tiny' actually has {flavor.vcpus} vCPU(s)"


# ---------------------------------------------------------------- exercise 1

def init_ex1(conn):
    conn.compute.update_quota_set(conn.current_project_id, instances=10, cores=20, ram=20 * 512)
    delete_instance_if_exists(conn, "ex1-vm")
    return True, "reset instance quota to 10 and cleared any leftover ex1-vm"


def check_ex1(conn):
    server = _server_active(conn, "ex1-vm")
    quota = conn.compute.get_quota_set(conn.current_project_id)
    if server is None:
        return False, f"no ex1-vm yet (current instance quota limit: {quota.instances})"
    if server.status == "ACTIVE":
        return True, f"ex1-vm is ACTIVE, instance quota limit is now {quota.instances}"
    return False, f"ex1-vm status={server.status}"


def _question_ex1(conn, answer: str):
    quota = conn.compute.get_quota_set(conn.current_project_id)
    try:
        given = int(answer.strip())
    except ValueError:
        return False, "answer should be a number"
    return given == quota.instances, f"the project's instance quota limit is currently {quota.instances}"


# ---------------------------------------------------------------- exercise 2

def init_ex2(conn):
    flavor = conn.compute.find_flavor("huge")
    if flavor is not None:
        conn.compute.delete_flavor(flavor.id)
    delete_instance_if_exists(conn, "ex2-vm")
    return True, "removed any leftover 'huge' flavor and ex2-vm"


def check_ex2(conn):
    server = _server_active(conn, "ex2-vm")
    if server is None:
        return False, "no instance named 'ex2-vm' found yet"
    if server.status == "ACTIVE":
        return True, "ex2-vm is ACTIVE"
    return False, f"ex2-vm status={server.status} task_state={server.task_state}"


# ---------------------------------------------------------------- exercise 3

def init_ex3(conn):
    image = find_unique_image(conn, "cirros")
    if image is not None and image.status != "active":
        conn.image.reactivate_image(image.id)
    delete_instance_if_exists(conn, "ex3-vm")
    return True, "made sure 'cirros' is active and cleared any leftover ex3-vm"


def check_ex3(conn):
    image = find_unique_image(conn, "cirros")
    server = _server_active(conn, "ex3-vm")
    if image is None:
        return False, "image 'cirros' is missing entirely -- run exercise 0's Init"
    if image.status != "active":
        return False, f"image 'cirros' status is '{image.status}', not active yet"
    if server is None:
        return False, "cirros is active, but no ex3-vm yet"
    if server.status == "ACTIVE":
        return True, "cirros is active and ex3-vm is ACTIVE"
    return False, f"ex3-vm status={server.status}"


# ---------------------------------------------------------------- exercise 4

def init_ex4(conn):
    subnet = conn.network.find_subnet("ex-subnet", project_id=conn.current_project_id)
    if subnet is not None:
        conn.network.update_subnet(
            subnet.id, allocation_pools=[{"start": "10.10.10.10", "end": "10.10.10.250"}],
        )
    delete_instance_if_exists(conn, "ex4-vm-a")
    delete_instance_if_exists(conn, "ex4-vm-b")
    return True, "restored ex-subnet's allocation pool and cleared any leftover ex4 instances"


def check_ex4(conn):
    a = _server_active(conn, "ex4-vm-a")
    b = _server_active(conn, "ex4-vm-b")
    a_ok = a is not None and a.status == "ACTIVE"
    b_ok = b is not None and b.status == "ACTIVE"
    if a_ok and b_ok:
        return True, "both ex4-vm-a and ex4-vm-b are ACTIVE"
    return False, f"ex4-vm-a={'ACTIVE' if a_ok else (a.status if a else 'missing')}, ex4-vm-b={'ACTIVE' if b_ok else (b.status if b else 'missing')}"


# ---------------------------------------------------------------- exercise 5

def init_ex5(conn):
    delete_instance_if_exists(conn, "ex5-vm")
    sg = conn.network.find_security_group("locked-down", project_id=conn.current_project_id)
    if sg is not None:
        conn.network.delete_security_group(sg.id)
    return True, "cleared any leftover ex5-vm and 'locked-down' security group"


def check_ex5(conn):
    server = _server_active(conn, "ex5-vm")
    if server is None:
        return False, "no instance named 'ex5-vm' found yet"
    if server.status != "ACTIVE":
        return False, f"ex5-vm status={server.status}"
    ports = list(conn.network.ports(device_id=server.id))
    for port in ports:
        for sg_id in port.security_group_ids:
            rules = list(conn.network.security_group_rules(security_group_id=sg_id))
            if any(r.direction == "ingress" and r.protocol == "tcp" and r.port_range_min == 22 for r in rules):
                return True, "ex5-vm is ACTIVE and reachable on tcp/22 through one of its security groups"
    return False, "ex5-vm is ACTIVE but none of its security groups allow tcp/22 ingress yet"


# ---------------------------------------------------------------- exercise 6

def init_ex6(conn):
    delete_instance_if_exists(conn, "ex6-vm")
    kp = conn.compute.find_keypair("temp-key")
    if kp is not None:
        conn.compute.delete_keypair(kp.id, ignore_missing=True)
    return True, "cleared any leftover ex6-vm and 'temp-key' keypair"


def check_ex6(conn):
    server = _server_active(conn, "ex6-vm")
    if server is None:
        return False, "no instance named 'ex6-vm' found yet"
    if server.status == "ACTIVE" and server.key_name:
        return True, f"ex6-vm is ACTIVE with key_name='{server.key_name}'"
    return False, f"ex6-vm status={server.status} key_name={server.key_name!r}"


def _question_ex6(conn, answer: str):
    return answer.strip() == "400", "booting with a keypair name that doesn't exist yet returns HTTP 400 (Bad Request) -- rejected before scheduling even starts"


# ---------------------------------------------------------------- exercise 7

def init_ex7(conn):
    delete_instance_if_exists(conn, "ex7-vm-a")
    delete_instance_if_exists(conn, "ex7-vm-b")
    sg = conn.compute.find_server_group("ex7-ag")
    if sg is not None:
        conn.compute.delete_server_group(sg.id, ignore_missing=True)
    return True, "cleared any leftover ex7 instances and 'ex7-ag' server group"


def check_ex7(conn):
    a = _server_active(conn, "ex7-vm-a")
    b = _server_active(conn, "ex7-vm-b")
    a_ok = a is not None and a.status == "ACTIVE"
    b_ok = b is not None and b.status == "ACTIVE"
    if a_ok and b_ok:
        return True, "both ex7-vm-a and ex7-vm-b are ACTIVE (b had to boot outside the strict group on this single-host cluster)"
    return False, f"ex7-vm-a={'ACTIVE' if a_ok else (a.status if a else 'missing')}, ex7-vm-b={'ACTIVE' if b_ok else (b.status if b else 'missing')}"


# ---------------------------------------------------------------- exercise 8

def init_ex8(conn):
    ok, out = ssh_exec.run("docker stop nova_compute")
    try:
        delete_instance_if_exists(conn, "ex8-vm", wait=False)
    except Exception:  # noqa: BLE001
        pass
    if not ok:
        return False, f"failed to stop nova_compute over SSH: {out}"
    return True, "stopped nova_compute on the VM (fault injected) and cleared any leftover ex8-vm"


def check_ex8(conn):
    ok, out = ssh_exec.run("docker ps --filter name=nova_compute --format '{{.Status}}'")
    if not ok:
        return False, f"couldn't check container status over SSH: {out}"
    if "Up" not in out:
        return False, f"nova_compute is not running yet (status: {out or 'not found'})"
    return True, f"nova_compute is back up (status: {out})"


# ---------------------------------------------------------------- exercise 9

def init_ex9(conn):
    ok, out = ssh_exec.run("docker restart neutron_openvswitch_agent")
    delete_instance_if_exists(conn, "ex9-vm")
    if not ok:
        return False, f"failed to restart the agent over SSH: {out}"
    return True, "restarted neutron_openvswitch_agent (fault window) and cleared any leftover ex9-vm"


def check_ex9(conn):
    server = _server_active(conn, "ex9-vm")
    if server is None:
        return False, "no instance named 'ex9-vm' found yet"
    if server.status != "ACTIVE":
        return False, f"ex9-vm status={server.status} task_state={server.task_state}"
    ports = list(conn.network.ports(device_id=server.id))
    if ports and all(p.status == "ACTIVE" for p in ports):
        return True, "ex9-vm is ACTIVE with its port bound"
    return False, f"ex9-vm is ACTIVE but its port status is {[p.status for p in ports]} -- try deleting and recreating the port, or reboot the instance"


EXERCISES: list[Exercise] = [
    Exercise(
        id="ex0", title="Exercise 0 -- Bootstrap & the happy path", tier=0, risk="none",
        goal="Get a real, reachable VM running from a completely empty project.",
        task=(
            "This project has nothing in it yet -- no instances, nothing running. Init has prepared "
            "a flavor, network, image, and keypair for you to work with.\n\n"
            "Get a real instance up and reachable: create one named exactly <code>ex0-vm</code>, and "
            "prove it actually works by logging into it once it's up."
        ),
        commands=[
            CommandRef("openstack server create", "Boots a new instance.", [
                ("--flavor <name>", "Which compute flavor (vCPU/RAM/disk) to use"),
                ("--image <name>", "Which Glance image to boot from"),
                ("--network <name>", "Which network to attach a port on"),
                ("--key-name <name>", "Which registered keypair to inject for SSH"),
                ("--security-group <name>", "Which security group to attach to the port"),
                ("<name>", "Name to give the new instance"),
            ]),
            CommandRef("openstack server show", "Displays full detail for one instance, including status and task_state.", [
                ("<name>", "The instance to inspect"),
            ]),
        ],
        warning="Creates real baseline resources (flavor/network/image/keypair) and an instance on your cluster.",
        hint="Poll <code>openstack server show ex0-vm</code> and watch the <code>OS-EXT-STS:task_state</code> field "
             "change between calls.",
        solution="Each flag on <code>server create</code> maps to a completely different service being called "
                 "behind the scenes: <code>--flavor</code>/<code>--image</code> are resolved to a flavor id and "
                 "an image id Nova already knows about (Glance is queried once, up front, just to confirm the "
                 "image exists and is active); <code>--network</code> tells Nova to ask Neutron for a port on "
                 "that network as part of the build; <code>--key-name</code> gets embedded into the instance's "
                 "metadata for cloud-init to write into <code>~/.ssh/authorized_keys</code> on first boot; "
                 "<code>--security-group</code> is attached to that same Neutron port. Watching "
                 "<code>server show</code> repeatedly, you'll see <code>task_state</code> move scheduling "
                 "&#8594; networking &#8594; spawning &#8594; (clears) as <code>vm_state</code> becomes "
                 "<code>active</code> -- literally the four services being called in sequence. Default cirros "
                 "login is user <code>cirros</code>, password <code>gocubsgo</code>, if the injected keypair "
                 "doesn't take for some reason.",
        init_fn=init_ex0, check_fn=check_ex0,
        question=Question("How many vCPUs does the 'tiny' flavor have?", _question_ex0),
    ),
    Exercise(
        id="ex1", title="Exercise 1 -- Quota exhaustion", tier=1, risk="low",
        goal="Diagnose and resolve a project-wide instance-creation failure.",
        task=(
            "New instance creation in this project has started failing outright -- no server ever "
            "gets created, and the error doesn't explain itself at a glance.\n\n"
            "Reproduce the failure so you can see the real error, work out what's actually blocking "
            "creation, resolve it, then prove the project can create instances again by booting one "
            "named <code>ex1-vm</code>."
        ),
        commands=[
            CommandRef("openstack quota show", "Shows current quota limits and usage for a project.", [
                ("<project>", "Project name or id (omit for your own current project)"),
            ]),
            CommandRef("openstack quota set", "Updates one or more quota limits for a project.", [
                ("--instances <N>", "Max number of instances allowed"),
                ("<project>", "Project to update"),
            ]),
        ],
        warning="Temporarily lowers your project's instance quota.",
        hint="Check usage with <code>quota show</code> before you touch anything -- you want to set the limit to "
             "exactly your current count, not to zero.",
        solution="<code>quota set --instances N &lt;project&gt;</code> writes directly into Nova's quota table "
                 "for that project id (the same id Keystone issued at auth time). The quota check is Nova's "
                 "very first validation step on a boot request -- it runs in nova-api before Neutron, Glance, "
                 "or Placement are ever contacted, which is why <code>OverQuota</code> is a clean, immediate "
                 "HTTP 403 with zero partial resources left behind. Contrast this with later failures "
                 "(exercises 2, 4) where something may already have been created before the failure hits.",
        init_fn=init_ex1, check_fn=check_ex1,
        question=Question("After fixing it, what is the project's instance quota limit?", _question_ex1),
    ),
    Exercise(
        id="ex2", title="Exercise 2 -- Oversized flavor / NoValidHost", tier=1, risk="low",
        goal="Diagnose a scheduling failure on an oversized request, then boot successfully.",
        task=(
            "A request to boot a very large instance is failing with a scheduling error, even though "
            "this box should have room for plenty of ordinary workloads.\n\n"
            "Reproduce the failure by asking for something clearly oversized, see exactly how it "
            "fails, then get a real instance named <code>ex2-vm</code> running using a size that "
            "actually fits."
        ),
        commands=[
            CommandRef("openstack flavor create", "Defines a new compute sizing template.", [
                ("--vcpus <N>", "vCPU count"),
                ("--ram <MB>", "RAM in megabytes"),
                ("--disk <GB>", "Root disk size in gigabytes"),
                ("<name>", "Flavor name"),
            ]),
            CommandRef("openstack server create", "Boots a new instance.", [
                ("--flavor <name>", "Which flavor to use -- the oversized one first, then 'tiny'"),
            ]),
        ],
        warning="Creates a throwaway flavor on your cluster.",
        hint="Triple digits on vCPUs/RAM/disk guarantees failure on this single-host box.",
        solution="Booting with the oversized flavor makes nova-scheduler ask Placement's "
                 "allocation-candidates endpoint for a resource provider (this single hypervisor) that can "
                 "satisfy that much VCPU/MEMORY_MB/DISK_GB inventory. Nothing qualifies, so the candidates "
                 "list comes back empty and Nova raises <code>NoValidHost</code> -- Placement itself never "
                 "'errors', it just reports no matches. This is a request-shape problem, not an "
                 "infrastructure problem: the fix is simply asking for less. Compare the real ceiling with "
                 "the hypervisor's actual reported capacity to see the mismatch directly.",
        init_fn=init_ex2, check_fn=check_ex2,
    ),
    Exercise(
        id="ex3", title="Exercise 3 -- Deactivated image", tier=1, risk="low",
        goal="Diagnose why boots from one image suddenly started failing, then fix it.",
        task=(
            "Instances built from the <code>cirros</code> image have suddenly started failing to "
            "boot, with nothing about the request itself having changed.\n\n"
            "Reproduce that failure, confirm it fails cleanly, then restore the image and boot an "
            "instance named <code>ex3-vm</code> successfully."
        ),
        commands=[
            CommandRef("openstack image set", "Modifies image metadata/state.", [
                ("--activate", "Marks the image active (bootable)"),
                ("--deactivate", "Marks the image deactivated (blocks new boots)"),
                ("<image>", "Image name or id"),
            ]),
        ],
        warning="Temporarily deactivates the shared 'cirros' test image used by other exercises.",
        hint="Same image name throughout -- you're just flipping its state and then flipping it back.",
        solution="<code>image set --deactivate</code> flips Glance's internal <code>status</code> field for "
                 "that image from <code>active</code> to <code>deactivated</code>, without deleting any data "
                 "-- exactly the mechanism you'd use to retire an image from new use while keeping it around "
                 "for already-running instances or audits. Nova checks this status before allowing a boot to "
                 "proceed to the image-download step; Glance returns 403 on the data download for a "
                 "deactivated image, and Nova surfaces that as a build failure. <code>--activate</code> simply "
                 "flips the field back.",
        init_fn=init_ex3, check_fn=check_ex3,
    ),
    Exercise(
        id="ex4", title="Exercise 4 -- Exhausted subnet", tier=2, risk="low",
        goal="Diagnose a network address-allocation failure, then resolve it.",
        task=(
            "A network that used to work fine for booting instances has started rejecting new ones "
            "with an address-allocation error.\n\n"
            "Reproduce this on <code>ex-subnet</code>, confirm a new boot (<code>ex4-vm-b</code>) "
            "fails once address space runs out (after a first instance, <code>ex4-vm-a</code>, took "
            "what was left), then free up address space and get <code>ex4-vm-b</code> booted "
            "successfully too."
        ),
        commands=[
            CommandRef("openstack subnet set", "Modifies an existing subnet.", [
                ("--allocation-pool start=<ip>,end=<ip>", "Replaces the subnet's entire IP allocation range"),
                ("<subnet>", "Subnet name or id"),
            ]),
        ],
        warning="Temporarily shrinks the shared 'ex-subnet' used by other exercises.",
        hint="Use the same IP for both start and end to leave exactly one free address, then widen the range "
             "back out afterward.",
        solution="<code>subnet set --allocation-pool</code> rewrites Neutron's IPAM (IP address management) "
                 "range for that subnet outright. When a boot needs a port, Neutron's IPAM tries to allocate "
                 "the next free address from that pool; with none left, it raises "
                 "<code>IpAddressGenerationFailure</code> and the port is never created at all -- which blocks "
                 "the boot before it ever reaches Nova's scheduler or the hypervisor. Widening the pool again "
                 "immediately unblocks new allocations, no restart of anything required.",
        init_fn=init_ex4, check_fn=check_ex4,
    ),
    Exercise(
        id="ex5", title="Exercise 5 -- Security group lockout", tier=2, risk="low",
        goal="Diagnose why a healthy-looking instance is completely unreachable.",
        task=(
            "A newly-launched instance reports as running and healthy in every OpenStack tool, but "
            "nobody can reach it over the network at all -- not even a ping.\n\n"
            "Reproduce this by booting <code>ex5-vm</code> into a security group with no rules, "
            "confirm it really is unreachable despite being <code>ACTIVE</code>, then fix "
            "reachability without needing to touch the instance itself."
        ),
        commands=[
            CommandRef("openstack security group create", "Creates a new, empty security group (no rules).", [
                ("<name>", "Security group name"),
            ]),
            CommandRef("openstack server create", "Boots a new instance.", [
                ("--security-group <name>", "Which security group to attach to the port"),
            ]),
            CommandRef("openstack security group rule create", "Adds one ingress/egress rule to a security group.", [
                ("--proto <tcp|udp|icmp>", "Protocol"),
                ("--dst-port <port>", "Destination port (tcp/udp only)"),
                ("--remote-ip <cidr>", "Source CIDR allowed to send this traffic"),
                ("<group>", "Security group to add the rule to"),
            ]),
            CommandRef("openstack port set", "Modifies an existing Neutron port.", [
                ("--security-group <name>", "Attach a different security group to the port, live, no reboot"),
                ("<port>", "Port id"),
            ]),
        ],
        warning="Creates a throwaway security group and an instance.",
        hint="Two independently valid fixes are listed above -- adding a rule to the locked-down group, or "
             "swapping the port's security group entirely. You only need one.",
        solution="Security group rules are enforced at the virtual switch/port level on the compute host, "
                 "entirely separate from Nova's own boot logic -- an instance with zero ingress rules boots, "
                 "schedules, and reaches <code>ACTIVE</code> completely normally, because none of that involves "
                 "checking firewall rules at all. The rules only matter the moment real traffic tries to reach "
                 "the port. That's why <code>port set --security-group</code> works as a live fix with no "
                 "reboot: you're changing which rule set the virtual switch enforces on that port right now, "
                 "not touching the instance itself.",
        init_fn=init_ex5, check_fn=check_ex5,
    ),
    Exercise(
        id="ex6", title="Exercise 6 -- Bad / missing keypair", tier=2, risk="low",
        goal="Diagnose an immediate boot rejection tied to SSH access, then fix it.",
        task=(
            "Someone tries to boot an instance with an SSH keypair name that isn't registered, and "
            "it's rejected immediately -- before anything is even scheduled.\n\n"
            "Reproduce that rejection, register the missing key, then boot an instance named "
            "<code>ex6-vm</code> that actually gets the right key injected for SSH access."
        ),
        commands=[
            CommandRef("openstack keypair create", "Registers an SSH keypair under a name.", [
                ("--public-key <path>", "Import an existing public key file instead of generating a new pair"),
                ("<name>", "Keypair name"),
            ]),
            CommandRef("openstack server create", "Boots a new instance.", [
                ("--key-name <name>", "Which registered keypair to inject"),
            ]),
        ],
        warning="Creates a throwaway keypair.",
        hint="You already have a real public key on this machine you can import, rather than generating a "
             "brand new pair.",
        solution="Unlike most scenarios in this set, this one fails before scheduling: Nova validates that the "
                 "named keypair exists as part of basic request validation, in the same early phase as the "
                 "quota check (exercise 1) -- an immediate HTTP 400, nothing scheduled, nothing partially "
                 "created. <code>keypair create --public-key</code> registers an existing SSH public key "
                 "under a name in Nova's own keypair table; at boot time, whatever key is registered under "
                 "the name you pass to <code>--key-name</code> gets embedded into the instance's metadata for "
                 "cloud-init to install.",
        init_fn=init_ex6, check_fn=check_ex6,
        question=Question("What HTTP status code do you get booting with a keypair name that doesn't exist?", _question_ex6),
    ),
    Exercise(
        id="ex7", title="Exercise 7 -- Anti-affinity violation", tier=3, risk="low",
        goal="Diagnose a placement failure that isn't actually about capacity.",
        task=(
            "Two instances that are supposed to never share a host keep failing to both come up -- "
            "the second one always errors out, even though the hypervisor clearly isn't full.\n\n"
            "Reproduce this with a strict placement policy (<code>ex7-vm-a</code> should succeed, "
            "<code>ex7-vm-b</code> should fail against the same policy), confirm the failure genuinely "
            "isn't about raw capacity, then get both instances running by working around the "
            "constraint appropriately."
        ),
        commands=[
            CommandRef("openstack server group create", "Creates a scheduler hint group.", [
                ("--policy <anti-affinity|affinity|soft-anti-affinity|soft-affinity>", "Placement policy for members"),
                ("<name>", "Group name"),
            ]),
            CommandRef("openstack server create", "Boots a new instance.", [
                ("--hint group=<group-id>", "Attaches this boot to a server group's placement policy -- omit entirely for an ordinary boot"),
            ]),
        ],
        warning="Creates a throwaway server group and two instances.",
        hint="Use the group hint for vm-a's attempt and for vm-b's failing attempt; then boot vm-b again with "
             "that flag simply left off.",
        solution="<code>--hint group=&lt;id&gt;</code> tells nova-scheduler to run the "
                 "<code>ServerGroupAntiAffinityFilter</code>, which rejects any host that already has another "
                 "member of that group -- on a single-host cluster, the second instance has nowhere to go and "
                 "gets <code>NoValidHost</code>, even though the host clearly has free capacity. This is a "
                 "policy rejection, not a capacity one; checking the hypervisor's free resources during this "
                 "exercise shows plenty of room the whole time. Distinguishing 'no room anywhere' from 'policy "
                 "says no' is the actual skill this exercise is testing.",
        init_fn=init_ex7, check_fn=check_ex7,
    ),
    Exercise(
        id="ex8", title="Exercise 8 -- Compute service down", tier=4, risk="medium",
        goal="Diagnose and recover from a cluster-wide instance-creation outage.",
        task=(
            "Instance creation across this entire project has suddenly stopped working, and it's not "
            "obvious why from the API responses alone. (Init has already reproduced this outage for "
            "you.)\n\n"
            "Track down which service is actually the problem, and bring it back."
        ),
        commands=[
            CommandRef("openstack compute service list", "Lists every Nova service (api/scheduler/conductor/compute) and its per-host state.", []),
            CommandRef("docker start / docker stop <container>", "Starts or stops a container directly on the host -- run over SSH, not through the OpenStack API.", []),
        ],
        warning="Stops the real nova_compute container on your only compute host until you restart it.",
        hint="The restore command is a Docker command run over the same SSH access used for the tunnel, not "
             "an OpenStack API call.",
        solution="<code>nova-compute</code> is the per-host agent that actually talks to libvirt and "
                 "periodically reports its host's capacity to Placement. Stopping its container doesn't touch "
                 "nova-api, nova-scheduler, or Placement at all -- they can look completely healthy while this "
                 "one piece is the entire problem, which is exactly why checking every service's status "
                 "independently is the very first layer of any real debugging pass, before looking at any "
                 "specific instance.",
        init_fn=init_ex8, check_fn=check_ex8,
    ),
    Exercise(
        id="ex9", title="Exercise 9 -- Neutron agent flakiness", tier=4, risk="medium",
        goal="Diagnose and recover a stuck, half-networked instance.",
        task=(
            "A newly-created instance can get stuck partway through coming up -- it never quite "
            "finishes setting up its networking, and normal troubleshooting doesn't explain why. "
            "(Init has just put the network agent through a rough restart to set this scenario up.)\n\n"
            "Boot an instance named <code>ex9-vm</code>, diagnose whether it actually got stuck, and "
            "get it to a genuinely working, reachable state."
        ),
        commands=[
            CommandRef("openstack port list", "Lists Neutron ports.", [
                ("--server <name>", "Filter to ports owned by one instance"),
            ]),
            CommandRef("openstack server create", "Boots a new instance.", []),
            CommandRef("openstack server delete", "Deletes an instance (and its ports) -- the actual recovery tool if a port is stuck.", [
                ("<name>", "Instance to delete"),
            ]),
        ],
        warning="Briefly restarts a real networking agent on your only host.",
        hint="If the port isn't ACTIVE, delete the whole instance and recreate it rather than trying to edit "
             "the port directly.",
        solution="Port binding requires a live L2 agent (openvswitch, here) on the compute host; during boot, "
                 "Nova creates the port and then waits for Neutron to send back a 'network-vif-plugged' event "
                 "before continuing. If that event's underlying RPC call gets lost because the agent bounced "
                 "at exactly the wrong moment, Neutron doesn't always retry it automatically, and the port "
                 "sits stuck in DOWN/BUILD indefinitely. Deleting and recreating the instance forces the whole "
                 "port-creation handshake to happen again from scratch against the now-healthy agent, which is "
                 "why that's the practical fix rather than trying to manually nurse the stuck port back to "
                 "life.",
        init_fn=init_ex9, check_fn=check_ex9,
    ),
]

EXERCISES_BY_ID = {e.id: e for e in EXERCISES}
