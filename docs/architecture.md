# IBLAI Infra Architecture

## Full Provisioning & Setup Flow

```mermaid
flowchart TB
    subgraph CLI["IBLAI INFRA CLI"]
        A[iblai infra provision] --> P1[AWS Credentials]
        P1 --> P2[Project & Compute Config]
        P2 --> P3[Network & SSH]
        P3 --> P4[Domain & Certificates]
        P4 --> P5[Review & Confirm]
    end

    subgraph TF["TERRAFORM (Phase 1: AWS Infrastructure)"]
        P5 --> TF1[VPC + Subnets + IGW]
        TF1 --> TF2[Security Groups]
        TF2 --> TF3[EC2 Instance - Ubuntu 22.04]
        TF3 --> TF4[Application Load Balancer]
        TF4 --> TF5[S3 Buckets - backups, media, static]
        TF5 --> TF6[ACM Certificates + DNS Validation]
        TF6 --> TF7[Route53 Records - 19 subdomains]
        TF7 --> TF8[HTTPS Listener + TLS 1.2]
    end

    subgraph SETUP["iblai infra setup (Phase 2)"]
        TF8 --> S1[Setup Prompts]
        S1 -->|SetupConfig| S2[SSH Verify]
        S2 --> S3[AnsibleRunner]
    end

    subgraph ANSIBLE["ANSIBLE PLAYBOOK (9 Roles on EC2)"]
        S3 --> R1

        subgraph INFRA_ROLES["Infrastructure Roles"]
            R1["1. Docker\nEngine + Compose + apache2-utils"]
            R1 --> R2["2. AWS CLI\nInstall + configure credentials"]
            R2 --> R3["3. Python\npyenv + Python 3.11.8 + venv"]
            R3 --> R4["4. ibl_cli_ops\nClone repo + pip install"]
        end

        subgraph CONFIG_ROLE["Platform Configuration"]
            R4 --> R5["5. ibl_platform\nchown /ibl/ → ubuntu\nConfigure domain, images, AI\nSet edX service image defaults\nLaunch reverse proxy\nCreate docker network\nGenerate Langfuse secrets\nECR login"]
        end

        subgraph SERVICE_ROLES["Service Launch Roles"]
            R5 --> R6["6. ibl_dm\nchown postgres data → UID 999\nibl dm launch"]
            R6 --> R7["7. ibl_edx\nibl edx launch"]
            R7 --> R8["8. ibl_spa\nGenerate OAuth2 creds\nCreate OAuth app in edX\nConfigure SPA settings\nLaunch Auth, Mentor, Skills SPAs"]
            R8 --> R9["9. final_steps\nibl launch --ibl-oauth --ibl-oidc\nibl dm auth-setup"]
        end
    end

    style CLI fill:#1a1a2e,color:#fff
    style TF fill:#16213e,color:#fff
    style SETUP fill:#0f3460,color:#fff
    style ANSIBLE fill:#533483,color:#fff
    style INFRA_ROLES fill:#2c2c54,color:#fff
    style CONFIG_ROLE fill:#2c2c54,color:#fff
    style SERVICE_ROLES fill:#2c2c54,color:#fff
```

## Containers Launched Per Role

```mermaid
flowchart LR
    subgraph PLATFORM["Role 5: Platform Config"]
        RP[Reverse Proxy]
    end

    subgraph DM["Role 6: IBL Manager"]
        DM_WEB[Web Server]
        DM_ASGI[ASGI Server]
        DM_WORKER[Celery Worker]
        DM_BEAT[Celery Beat]
        DM_PG[PostgreSQL]
        DM_REDIS[Redis]
    end

    subgraph EDX["Role 7: Open edX"]
        LMS[LMS]
        CMS[CMS]
        LMS_W[LMS Worker]
        CMS_W[CMS Worker]
        MYSQL[MySQL]
        REDIS2[Redis]
        MONGO[MongoDB]
        ES[Elasticsearch]
        FORUM[Forum]
        NOTES[Notes]
        MEILI[Meilisearch]
        CADDY[Caddy]
        SMTP[SMTP Relay]
        PERMS[Permissions]
    end

    subgraph SPA["Role 8: SPA Services"]
        AUTH[Auth SPA]
        MENTOR[Mentor SPA]
        SKILLS[Skills SPA]
    end

    subgraph FINAL["Role 9: Final Steps"]
        OAUTH[OAuth2 Server]
        OIDC[OIDC Provider]
    end

    style PLATFORM fill:#e74c3c,color:#fff
    style DM fill:#3498db,color:#fff
    style EDX fill:#2ecc71,color:#fff
    style SPA fill:#9b59b6,color:#fff
    style FINAL fill:#f39c12,color:#fff
```

## Network & DNS Architecture

```mermaid
flowchart TB
    USER[User Browser] --> ALB[AWS Application Load Balancer]

    ALB --> |learn| LMS
    ALB --> |studio.learn| CMS
    ALB --> |api.data / web.data| DM_WEB[IBL Manager]
    ALB --> |auth| AUTH[Auth SPA]
    ALB --> |mentorai| MENTOR[Mentor SPA]
    ALB --> |skillsai| SKILLS[Skills SPA]
    ALB --> |monitor| MONITOR[Monitoring]
    ALB --> |prometheus| PROM[Prometheus]
    ALB --> |flowise| FLOWISE[Flowise]

    subgraph EC2["EC2 Instance"]
        RP[Reverse Proxy] --> LMS[Open edX LMS]
        RP --> CMS[Open edX CMS]
        RP --> DM_WEB
        RP --> AUTH
        RP --> MENTOR
        RP --> SKILLS
        RP --> MONITOR
        RP --> PROM
        RP --> FLOWISE
    end

    subgraph CERT1["ACM Certificate 1"]
        C1["learn, studio.learn, apps.learn\nmeilisearch.learn, preview.learn\napi.data, asgi.data, llm.data\nmentor.data, api, web.data"]
    end

    subgraph CERT2["ACM Certificate 2"]
        C2["base.manager, auth, mentorai\nskillsai, monitor, flowise\nplatform, prometheus"]
    end

    ALB -.-> CERT1
    ALB -.-> CERT2

    style ALB fill:#ff6b6b,color:#fff
    style EC2 fill:#4ecdc4,color:#fff
    style CERTS fill:#45b7d1,color:#fff
```

## Render This Diagram

```bash
# Using mermaid-cli
npx @mermaid-js/mermaid-cli -i docs/architecture.md -o docs/architecture.png

# Or just view on GitHub — Mermaid renders natively in .md files
```
