# Deploying RouteOS on AWS (free tier) — from a new account to a live HTTPS URL

Written for someone who has never opened the AWS console. Every step says **why**, because the
"why" is what interviews ask about. Budget 60–90 minutes the first time.

**Architecture:** one EC2 instance in a public subnet running the whole stack with Docker Compose
behind Caddy, which gets you free HTTPS. Postgres/PostGIS and Redis run as containers on the same
box, reachable only on the private Docker network.

```
Internet ──► Security Group (22, 80, 443 only)
                    │
              EC2 t3.micro  (public subnet, public IPv4)
                    │
                 Caddy :443  ── TLS from Let's Encrypt, auto-renewed
                 ├── /api/*, /ws/*  ─► FastAPI (uvicorn :8000)
                 └── everything else ─► nginx (built React SPA)
                          │
                 postgres (PostGIS) + redis   ── no published ports
```

**Why one instance instead of RDS + ElastiCache + ALB?** An Application Load Balancer is about
$16/month and a NAT Gateway about $32/month, and **neither is in the free tier**. This design
keeps the bill at $0. Section 9 covers how you would split it apart for production — that is the
version worth describing in an interview.

---

## 1. Create the account

1. Go to <https://aws.amazon.com> and choose **Create an AWS Account**.
2. You need an email, a password, and **a credit or debit card**. AWS makes a small temporary
   authorisation (about $1) to verify the card and refunds it. Indian debit cards usually work;
   some fail at the 3-D Secure step, and the only fix is a different card.
3. Verify your phone by SMS or voice call.
4. Choose the **Basic support plan — free**. Do not pick Developer ($29/month).

> **Free tier terms changed during 2025.** New accounts may get a credit-based plan rather than
> the classic "12 months free". Do not trust any blog on this, including this file — open
> **Billing and Cost Management → Free tier** in your own console and read what your account
> actually has. The safety net in step 2 matters either way.

## 2. Before anything else: cost guardrails

This is the step people skip and then write horror stories about. Do it first.

1. **Billing alarm.** Console → **Billing and Cost Management** → **Budgets** → *Create budget* →
   pick the **Zero spend budget** template → enter your email → Create. You now get an email the
   moment the account is forecast to cost anything at all.
2. **Let IAM users see billing.** Billing → *Account* → *IAM user and role access to Billing
   Information* → activate. Otherwise only root can see the bill.
3. Bookmark **Billing → Free tier**, which tracks your usage against each allowance.

**The four things that actually cause surprise bills:**

| Trap | Cost | How to avoid it |
|---|---|---|
| **NAT Gateway** | ~$32/mo, not free tier | Never create one. Keep the instance in a **public** subnet. |
| **Load balancer (ALB/NLB)** | ~$16/mo, not free | Not needed — Caddy terminates TLS on the instance. |
| **Unattached Elastic IP** | ~$3.60/mo each | Release any Elastic IP you are not using. |
| **Orphaned EBS volumes and snapshots** | per GB-month | Terminating an instance can leave the volume behind. Check **EC2 → Volumes** for ones in the `Available` state. |

Also note that since 2024 **every public IPv4 address is billed** at about $0.005/hour (~$3.60 a
month). The free tier includes an allowance covering one instance, and that allowance is one of
the things that expires — so check the Free tier page rather than assuming.

## 3. Secure the root user

The email and password you just created is the **root user**. It can close the account and cannot
be restricted by any policy, so lock it away and stop using it.

1. **Enable MFA on root.** Account menu (top right) → **Security credentials** →
   *Multi-factor authentication* → Assign MFA device → **Authenticator app** (Google Authenticator
   or Authy). Scan the QR code and enter two consecutive codes.
2. **Create an admin user for yourself.** **IAM** → *Users* → Create user → name it → tick
   *Provide user access to the console* → attach the **AdministratorAccess** policy → Create.
   Save the sign-in URL, which looks like `https://<account-id>.signin.aws.amazon.com/console`.
3. Sign out of root and use the IAM user from now on. Enable MFA on that user too.

> **How to say this in an interview:** "Root is only for account-level operations — billing,
> changing the support plan, closing the account. Everything else goes through IAM identities, and
> anything running *inside* AWS uses an IAM **role** with temporary credentials rather than a user
> with long-lived access keys."

## 4. Choose a region

Use the region selector at the top right and pick the one nearest your users —
**ap-south-1 (Mumbai)** for India. Region affects latency, price, and which services are
available. Free tier is per account, not per region, but **resources are region-scoped**: an
instance you created in Mumbai is invisible if the console is set to N. Virginia. This causes a
lot of "where did my instance go" confusion.

## 5. Launch the EC2 instance

**EC2 → Instances → Launch instances.**

| Field | Value | Why |
|---|---|---|
| Name | `routeos` | |
| AMI | **Ubuntu Server 24.04 LTS**, 64-bit x86 | Must be labelled *Free tier eligible*. |
| Instance type | **t3.micro** (or `t2.micro` if that is the one your account marks free) | 2 burstable vCPUs, 1 GB RAM. |
| Key pair | *Create new key pair* → name `routeos-key` → **RSA** → **.pem** → Download | This is the **only** copy. Lose it and you lose SSH access. |
| Network | Default VPC, **Auto-assign public IP = Enable** | Public subnet, so no NAT Gateway is needed. |
| Security group | Create new — rules below | |
| Storage | **20 GiB gp3** | Free tier allows 30 GB. Docker images are large; 8 GB fills up. |
| Advanced → User data | paste [`ec2-user-data.sh`](ec2-user-data.sh) | Installs Docker and adds swap on first boot. |

**Inbound security group rules:**

| Type | Port | Source | Why |
|---|---|---|---|
| SSH | 22 | **My IP** | Never `0.0.0.0/0` — it gets brute-forced within minutes. |
| HTTP | 80 | `0.0.0.0/0` | Let's Encrypt validates over port 80. |
| HTTPS | 443 | `0.0.0.0/0` | The actual application. |

Leave outbound as the default (all traffic allowed). Launch, then wait for
**2/2 status checks passed**.

Notice there is no rule for 5432 or 6379. The database and cache are reachable only on the Docker
network. That is the most important property of this design to be able to point at: the attack
surface is three ports, and one of them is restricted to your own IP.

## 6. Connect and deploy

Copy the instance's **Public IPv4 address**, for example `13.51.2.9`.

**SSH from Windows PowerShell** (Windows 10 and later ship an SSH client). The `.pem` file must
not be readable by other users or SSH refuses it — on Windows that means fixing the ACL, not
running `chmod`:

```powershell
cd $HOME\Downloads
icacls routeos-key.pem /inheritance:r
icacls routeos-key.pem /grant:r "$($env:USERNAME):(R)"
ssh -i routeos-key.pem ubuntu@13.51.2.9
```

The first boot runs the user-data script. If `docker` is not available yet, give it a minute and
watch `tail -f /var/log/cloud-init-output.log`. Then, on the instance:

```bash
git clone https://github.com/dhanoliya-ji/RouteOS.git
cd RouteOS

# A hostname that resolves to this IP with zero DNS setup.
# sslip.io resolves <anything>.<ip-with-dashes>.sslip.io to that IP, which is
# enough for Let's Encrypt to issue a real certificate.
export SITE_ADDRESS=routeos.13-51-2-9.sslip.io      # your IP, dashes not dots
export ACME_EMAIL=you@example.com

cp .env.example .env
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(openssl rand -hex 16)|" .env
sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" .env

# Persist both values so later logins and reboots still see them
echo "SITE_ADDRESS=$SITE_ADDRESS" >> .env
echo "ACME_EMAIL=$ACME_EMAIL" >> .env

docker compose -f aws/docker-compose.aws.yml up -d --build
```

The build takes **8–15 minutes** on a t3.micro — the `ortools` wheel is large, and this is exactly
what the swap file is for. Follow it with:

```bash
docker compose -f aws/docker-compose.aws.yml logs -f backend
```

You want to see `Running Alembic migrations`, then `Seeded users, 1 depot, 20 vehicles`, then
`Uvicorn running`.

Now open **`https://routeos.13-51-2-9.sslip.io`** and sign in with `dispatcher@routeos.dev` /
`dispatch12345`. Caddy requests the certificate on the first hit, so the very first page load can
take a few seconds.

Checks worth running:

```bash
curl -s https://$SITE_ADDRESS/health     # {"status":"ok","database":"up","redis":"up"}
docker compose -f aws/docker-compose.aws.yml ps
free -h                                  # confirm the swap file is active
```

## 7. What to expect on a t3.micro

`t3.micro` is a **burstable** instance: it earns CPU credits at roughly 10% of a vCPU as a
baseline and spends them when busy. That matters here because the OR-Tools solve is CPU-bound —
the same constraint that shaped the solver design (see
[the note in the README](../README.md#a-note-on-the-live-demo)).

- Idle and normal UI use are comfortable.
- Each optimization run spends credits. That is why the compose file sets
  `SOLVER_ASYNC_TIME_LIMIT_SECONDS=120` and `MAX_CONCURRENT_OPTIMIZATION_JOBS=1`.
- Watch **CloudWatch → Metrics → EC2 → CPUCreditBalance**. If it reaches zero the instance is
  throttled to baseline and everything crawls.
- Keep **Unlimited mode disabled** on a free-tier instance, otherwise bursting past your credit
  balance is billed as surplus credits.

## 8. Day-2 operations

```bash
# Deploy the latest code
cd ~/RouteOS && git pull && docker compose -f aws/docker-compose.aws.yml up -d --build

# Logs, restart, stop
docker compose -f aws/docker-compose.aws.yml logs -f --tail=100
docker compose -f aws/docker-compose.aws.yml restart backend
docker compose -f aws/docker-compose.aws.yml down          # keeps the data volume

# Back up the database
docker compose -f aws/docker-compose.aws.yml exec -T postgres \
  pg_dump -U routeos routeos | gzip > ~/routeos-$(date +%F).sql.gz

# Reclaim disk after a few rebuilds — layers add up fast on a 20 GB volume
docker system prune -af
```

**Stopping vs terminating.** *Stop* ends compute billing but keeps the EBS volume (which still
counts against the 30 GB allowance) and **releases the public IP**, so the address changes on
restart and your `SITE_ADDRESS` breaks. *Terminate* deletes the instance and, by default, its
root volume.

## 9. The production version

Worth being able to sketch, because "why didn't you use X?" is a standard follow-up.

| Concern | This free-tier build | Production build |
|---|---|---|
| Database | Postgres container on the instance | **RDS PostgreSQL** with PostGIS, Multi-AZ, automated backups, in private subnets |
| Cache | Redis container | **ElastiCache** for Redis |
| Frontend | nginx container | **S3 + CloudFront** — static assets on a CDN, cheaper and faster |
| TLS and routing | Caddy on the box | **ALB** with an **ACM** certificate, instances in private subnets |
| Compute | One hand-launched EC2 | **ECS Fargate** or an Auto Scaling Group, image stored in **ECR** |
| The solver | In-process background job | **SQS queue + worker service** scaled separately — it is CPU-bound and should not share a box with the API |
| Secrets | `.env` file on disk | **Secrets Manager** or SSM **Parameter Store**, injected at startup |
| Access | SSH with a `.pem` | **SSM Session Manager** — no open port 22 and no key to lose |
| Logs | `docker logs` | **CloudWatch Logs** |

The honest summary: this build has one instance, so it has a single point of failure and no
horizontal scaling. That is the right trade for a portfolio demo, and knowing *why* it is the
wrong trade for production is the actual point.

## 10. Tearing it down

1. **EC2 → Instances** → select → *Instance state* → **Terminate**.
2. **EC2 → Volumes** → delete anything left in the `Available` state.
3. **EC2 → Elastic IPs** → release any you allocated.
4. Check **Billing → Free tier** a day later to confirm nothing is still accruing.

---

## Interview crib sheet

The concepts this deployment actually exercises, phrased the way someone who has done it would
answer rather than someone who has read about it.

**Region vs Availability Zone.** A region is a geographic area (`ap-south-1`); an AZ is one or
more discrete datacentres within it (`ap-south-1a`). Fault tolerance comes from spreading across
AZs. My single instance lives in one AZ, which is precisely why it is not production-ready.

**VPC, subnet, route table.** A VPC is your private network. A subnet is **public** when its route
table sends `0.0.0.0/0` to an **Internet Gateway**, and **private** when it does not. A private
subnet needs a **NAT Gateway** for outbound traffic, which costs about $32/month — so I
deliberately used a public subnet with a tight security group instead.

**Security group vs NACL.** A security group is **stateful** and attaches to an instance: allow
inbound 443 and the response is automatically permitted. A NACL is **stateless**, attaches to a
subnet, evaluates numbered rules in order, and supports explicit **deny** rules. Security groups
are allow-only. I used security group rules for 22/80/443 and left the NACL at its default.

**IAM user vs role.** A user has long-lived credentials. A role is *assumed* and yields
**temporary** credentials. Anything running on EC2 should use an **instance profile** — a role —
so there is no access key sitting in a file waiting to leak.

**EC2 vs ECS/Fargate vs Lambda.** With EC2 you manage the OS. Fargate runs containers with no
servers to manage. Lambda is event-driven with a 15-minute execution cap. My solver runs for
minutes and is CPU-bound, so Lambda is a poor fit; Fargate would be the natural next step.

**Burstable instances.** T-family instances earn CPU credits at a baseline rate and spend them
under load; exhaust them and you are throttled to baseline. This is directly relevant here,
because the VRP solve is CPU-bound and its time budget is tuned to the hardware it runs on.

**EBS vs S3 vs instance store.** EBS is network block storage attached to one instance and
persists independently of it. S3 is object storage addressed over HTTP. Instance store is physical
disk on the host and is **lost when the instance stops**. Postgres data lives on EBS through a
Docker volume.

**Why AWS bills surprise people.** NAT Gateways, load balancers, unattached Elastic IPs, orphaned
EBS volumes and snapshots, and cross-AZ data transfer. A zero-spend budget alert on day one is the
cheapest insurance available.
