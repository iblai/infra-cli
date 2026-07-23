# iblai-infra-ops Architecture

## Single Server — AWS Infrastructure

```mermaid
flowchart TB
    INTERNET[Internet] --> R53

    subgraph AWS["AWS Cloud"]

        subgraph R53_BLOCK["Route53"]
            R53[Hosted Zone\n19 subdomain A records]
        end

        R53 --> ALB

        subgraph VPC["VPC (10.0.0.0/16)"]

            subgraph PUB1["Public Subnet 1 (AZ-a)"]
                ALB[Application Load Balancer\nHTTP :80 → HTTPS redirect\nHTTPS :443 → Target Group\nTLS 1.2+ policy]
            end

            subgraph PUB2["Public Subnet 2 (AZ-b)"]
                ALB_NODE[ALB Node]
            end

            ALB --> EC2

            subgraph EC2_BLOCK["EC2 Instance (t3.2xlarge)"]
                EC2[Ubuntu 22.04\n50GB gp3 encrypted]

                subgraph DOCKER["Docker Containers"]
                    RP[Reverse Proxy\nCaddy]

                    subgraph EDX["iblai-edx-pro"]
                        LMS[LMS]
                        CMS[CMS]
                        LMS_W[LMS Worker]
                        CMS_W[CMS Worker]
                        MYSQL[(MySQL)]
                        REDIS_E[(Redis)]
                        MONGO[(MongoDB)]
                        ES[(Elasticsearch)]
                    end

                    subgraph DM["iblai-dm-pro"]
                        WEB[Web]
                        ASGI[ASGI]
                        CELERY[Celery Worker]
                        BEAT[Celery Beat]
                        PG[(PostgreSQL)]
                        REDIS_D[(Redis)]
                    end

                    subgraph SPA["iblai-web-frontend"]
                        AUTH[Auth]
                        MENTOR[Mentor]
                        SKILLS[Skills]
                    end
                end
            end

            subgraph SG["Security Groups"]
                SG_ALB[ALB SG\nHTTP/HTTPS from 0.0.0.0/0]
                SG_EC2[EC2 SG\nSSH from VPN IP\nHTTP from ALB only]
            end
        end

        subgraph ACM["ACM Certificates"]
            CERT1[Certificate 1\napi.data, learn, studio.learn\napps.learn, preview.learn\nasgi.data, llm.data\napi, base.manager]
            CERT2[Certificate 2\nauth, os, lms\nmonitor, flowise, platform\nprometheus, studio.learn\nmeilisearch.learn]
        end

        ALB -.->|TLS termination| CERT1
        ALB -.->|TLS termination| CERT2

        subgraph S3["S3 Buckets"]
            S3_BACKUP[Backups]
            S3_MEDIA[DM Media]
            S3_STATIC[DM Static\nPublic read]
        end

        EC2 -.-> S3

    end

    style AWS fill:#232f3e,color:#fff
    style VPC fill:#1a472a,color:#fff
    style PUB1 fill:#2d6a4f,color:#fff
    style PUB2 fill:#2d6a4f,color:#fff
    style EC2_BLOCK fill:#3a506b,color:#fff
    style DOCKER fill:#1c2541,color:#fff
    style EDX fill:#2ecc71,color:#fff
    style DM fill:#3498db,color:#fff
    style SPA fill:#9b59b6,color:#fff
    style SG fill:#4a4e69,color:#fff
    style ACM fill:#e07a5f,color:#fff
    style S3 fill:#f2cc8f,color:#000
    style R53_BLOCK fill:#45b7d1,color:#fff
```

## Multi Server — AWS Infrastructure

```mermaid
flowchart TB
    INTERNET[Internet] --> R53

    subgraph AWS["AWS Cloud"]

        subgraph R53_BLOCK["Route53"]
            R53[Hosted Zone\nSubdomain A records]
        end

        R53 --> ALB

        subgraph VPC["VPC (10.0.0.0/16)"]

            subgraph PUB1["Public Subnet 1 (AZ-a)"]
                ALB[Application Load Balancer\nHTTPS :443 with TLS 1.2+]
            end

            subgraph PUB2["Public Subnet 2 (AZ-b)"]
                ALB_NODE[ALB Node]
            end

            ALB --> APP_NODE
            ALB --> APP_NODE_2

            subgraph APP_SUBNET["Application Nodes"]

                subgraph APP1["App Node 1 (EC2)"]
                    APP_NODE[Ubuntu 22.04]
                    subgraph APP1_DOCKER["Docker"]
                        RP1[Reverse Proxy]
                        LMS1[iblai-edx-pro\nLMS + CMS + Workers]
                        DM1[iblai-dm-pro\nWeb + ASGI + Celery]
                        SPA1[iblai-web-frontend\nAuth + Mentor + Skills]
                    end
                end

                subgraph APP2["App Node 2 (EC2)"]
                    APP_NODE_2[Ubuntu 22.04]
                    subgraph APP2_DOCKER["Docker"]
                        RP2[Reverse Proxy]
                        LMS2[iblai-edx-pro\nLMS + CMS + Workers]
                        DM2[iblai-dm-pro\nWeb + ASGI + Celery]
                        SPA2[iblai-web-frontend\nAuth + Mentor + Skills]
                    end
                end

            end

            subgraph DATA_SUBNET["Data Nodes"]

                subgraph DATA1["Data Node 1 (EC2)"]
                    MYSQL[(MySQL\nPrimary)]
                    PG[(PostgreSQL\nPrimary)]
                    REDIS[(Redis)]
                    MONGO[(MongoDB)]
                    ES[(Elasticsearch)]
                end

                subgraph DATA2["Data Node 2 (EC2)"]
                    MYSQL_R[(MySQL\nReplica)]
                    PG_R[(PostgreSQL\nReplica)]
                    REDIS_R[(Redis\nReplica)]
                end

            end

            APP_NODE -.-> DATA1
            APP_NODE_2 -.-> DATA1
            DATA1 -.->|Replication| DATA2

            subgraph SG["Security Groups"]
                SG_ALB[ALB SG]
                SG_APP[App SG\nHTTP from ALB\nSSH from VPN]
                SG_DATA[Data SG\nDB ports from App SG only]
            end
        end

        subgraph ACM["ACM Certificates"]
            CERT1[Certificate 1]
            CERT2[Certificate 2]
        end

        ALB -.-> CERT1
        ALB -.-> CERT2

        subgraph S3["S3 Buckets"]
            S3_BACKUP[Backups]
            S3_MEDIA[DM Media]
            S3_STATIC[DM Static]
        end

        APP_NODE -.-> S3

    end

    style AWS fill:#232f3e,color:#fff
    style VPC fill:#1a472a,color:#fff
    style PUB1 fill:#2d6a4f,color:#fff
    style PUB2 fill:#2d6a4f,color:#fff
    style APP_SUBNET fill:#3a506b,color:#fff
    style DATA_SUBNET fill:#4a4e69,color:#fff
    style APP1 fill:#1c2541,color:#fff
    style APP2 fill:#1c2541,color:#fff
    style DATA1 fill:#2c2c54,color:#fff
    style DATA2 fill:#2c2c54,color:#fff
    style SG fill:#4a4e69,color:#fff
    style ACM fill:#e07a5f,color:#fff
    style S3 fill:#f2cc8f,color:#000
    style R53_BLOCK fill:#45b7d1,color:#fff
```

## Provisioning & Setup Flow

```mermaid
flowchart TB
    subgraph CLI["iblai-infra-ops"]
        A[iblai infra provision] --> P1[AWS Credentials]
        P1 --> P2[Project & Compute Config]
        P2 --> P3[Network & SSH]
        P3 --> P4[Domain & Certificates]
        P4 --> P5[Review & Confirm]
    end

    subgraph TF["Terraform (AWS Infrastructure)"]
        P5 --> TF1[VPC + Subnets]
        TF1 --> TF2[Security Groups]
        TF2 --> TF3[EC2 Instance]
        TF3 --> TF4[Application Load Balancer]
        TF4 --> TF5[S3 Buckets]
        TF5 --> TF6[ACM Certificates]
        TF6 --> TF7[Route53 DNS Records]
        TF7 --> TF8[HTTPS Listener]
    end

    subgraph SETUP["Ansible (Platform Setup)"]
        TF8 --> S1[Setup Prompts]
        S1 --> S2[SSH Verify]
        S2 --> S3[Ansible Runner]
    end

    subgraph ANSIBLE["Ansible Playbook (9 Roles)"]
        S3 --> R1

        subgraph INFRA_ROLES["Infrastructure Roles"]
            R1["1. Docker Engine"]
            R1 --> R2["2. AWS CLI Setup"]
            R2 --> R3["3. Python Virtual Env"]
            R3 --> R4["4. iblai-cli-ops"]
        end

        subgraph CONFIG_ROLE["Platform Configuration"]
            R4 --> R5["5. Platform Config"]
        end

        subgraph SERVICE_ROLES["Service Launch Roles"]
            R5 --> R6["6. iblai-dm-pro"]
            R6 --> R7["7. iblai-edx-pro"]
            R7 --> R8["8. iblai-web-frontend"]
            R8 --> R9["9. Final Steps"]
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

## Containers Per Role

```mermaid
flowchart LR
    subgraph PLATFORM["Role 5: Platform Config"]
        RP[Reverse Proxy]
    end

    subgraph DM["Role 6: iblai-dm-pro"]
        DM_WEB[Web Server]
        DM_ASGI[ASGI Server]
        DM_WORKER[Celery Worker]
        DM_BEAT[Celery Beat]
        DM_PG[PostgreSQL]
        DM_REDIS[Redis]
    end

    subgraph EDX["Role 7: iblai-edx-pro"]
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

    subgraph SPA["Role 8: iblai-web-frontend"]
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

## Render These Diagrams

```bash
# Using mermaid-cli
npx @mermaid-js/mermaid-cli -i docs/architecture.md -o docs/architecture.png

# Or just view on GitHub — Mermaid renders natively in .md files
```
