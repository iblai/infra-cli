# ibl.ai Infrastructure - Call Server (LiveKit)
# Provisions: isolated VPC, EC2 with Elastic IP, full LiveKit port set,
# optional Route53 A record. No ALB (LiveKit needs direct UDP/TCP).

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  required_version = ">= 1.0"
}

provider "aws" {
  region = var.region
}

# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ---------------------------------------------------------------------------
# Locals
# ---------------------------------------------------------------------------

locals {
  resource_prefix = "${var.project_name}-${var.environment}"
  use_route53     = var.hosted_zone_id != ""
}

# ---------------------------------------------------------------------------
# Isolated VPC
# ---------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "${local.resource_prefix}-call-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${local.resource_prefix}-call-igw" }
}

# Two public subnets (multi-AZ required in case we ever put an NLB in front).
# The call server itself lives in subnet index 0.
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${local.resource_prefix}-call-public-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.resource_prefix}-call-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ---------------------------------------------------------------------------
# Security Group — LiveKit port set
# ---------------------------------------------------------------------------
# Ports from https://docs.livekit.io/transport/self-hosting/ports-firewall/
# Core ports always open. SIP ports gated on var.enable_sip.

resource "aws_security_group" "call" {
  name        = "${local.resource_prefix}-call-sg"
  description = "Call server (LiveKit) - API, ICE, TURN, optional SIP"
  vpc_id      = aws_vpc.main.id

  # --- SSH ---
  ingress {
    description = "SSH from VPN"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["${var.vpn_ip}/32"]
  }

  # --- HTTP (for Let's Encrypt HTTP-01 challenges) ---
  ingress {
    description = "HTTP (ACME challenge / redirects)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # --- HTTPS (TURN/TLS fallback on 443, also general web) ---
  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # --- LiveKit API / WebSocket ---
  ingress {
    description = "LiveKit API / WebSocket"
    from_port   = 7880
    to_port     = 7880
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # --- LiveKit ICE/TCP fallback ---
  ingress {
    description = "LiveKit ICE/TCP"
    from_port   = 7881
    to_port     = 7881
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # --- LiveKit ICE/UDP Mux ---
  ingress {
    description = "LiveKit ICE/UDP mux"
    from_port   = 7882
    to_port     = 7882
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # --- LiveKit ICE/UDP host candidates ---
  ingress {
    description = "LiveKit ICE/UDP host candidates"
    from_port   = 50000
    to_port     = 60000
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # --- TURN/TLS ---
  ingress {
    description = "LiveKit TURN/TLS"
    from_port   = 5349
    to_port     = 5349
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # --- TURN/UDP (also STUN) ---
  ingress {
    description = "LiveKit TURN/UDP + STUN"
    from_port   = 3478
    to_port     = 3478
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # --- SIP stack (optional) ---
  dynamic "ingress" {
    for_each = var.enable_sip ? [1] : []
    content {
      description = "LiveKit SIP signaling UDP"
      from_port   = 5060
      to_port     = 5060
      protocol    = "udp"
      cidr_blocks = ["0.0.0.0/0"]
    }
  }

  dynamic "ingress" {
    for_each = var.enable_sip ? [1] : []
    content {
      description = "LiveKit SIP signaling TCP"
      from_port   = 5060
      to_port     = 5060
      protocol    = "tcp"
      cidr_blocks = ["0.0.0.0/0"]
    }
  }

  dynamic "ingress" {
    for_each = var.enable_sip ? [1] : []
    content {
      description = "LiveKit SIP signaling TLS"
      from_port   = 5061
      to_port     = 5061
      protocol    = "tcp"
      cidr_blocks = ["0.0.0.0/0"]
    }
  }

  dynamic "ingress" {
    for_each = var.enable_sip ? [1] : []
    content {
      description = "LiveKit SIP RTP media"
      from_port   = 10000
      to_port     = 20000
      protocol    = "udp"
      cidr_blocks = ["0.0.0.0/0"]
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.resource_prefix}-call-sg" }
}

# ---------------------------------------------------------------------------
# SSH Key Pair
# ---------------------------------------------------------------------------

resource "aws_key_pair" "main" {
  count      = var.create_key_pair ? 1 : 0
  key_name   = "${local.resource_prefix}-call-key"
  public_key = var.ssh_public_key

  tags = { Name = "${local.resource_prefix}-call-key" }
}

# ---------------------------------------------------------------------------
# EC2 Instance + Elastic IP
# ---------------------------------------------------------------------------

resource "aws_instance" "main" {
  ami                    = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = var.create_key_pair ? aws_key_pair.main[0].key_name : var.existing_key_pair_name
  vpc_security_group_ids = [aws_security_group.call.id]
  subnet_id              = aws_subnet.public[0].id

  user_data = var.skip_user_data ? null : templatefile("${path.module}/user_data.sh", {
    enable_sip = var.enable_sip
  })

  root_block_device {
    volume_type = var.root_volume_type
    volume_size = var.root_volume_size
    encrypted   = true
  }

  tags = { Name = "${local.resource_prefix}-call-server" }
}

# Stable public IP — DNS points here, doesn't rotate on stop/start.
resource "aws_eip" "main" {
  instance = aws_instance.main.id
  domain   = "vpc"

  tags = { Name = "${local.resource_prefix}-call-eip" }

  depends_on = [aws_internet_gateway.main]
}

# ---------------------------------------------------------------------------
# DNS (optional — only when hosted_zone_id is provided)
# ---------------------------------------------------------------------------

data "aws_route53_zone" "main" {
  count   = local.use_route53 ? 1 : 0
  zone_id = var.hosted_zone_id
}

resource "aws_route53_record" "call" {
  count = local.use_route53 ? 1 : 0

  allow_overwrite = true
  zone_id         = data.aws_route53_zone.main[0].zone_id
  name            = var.base_domain
  type            = "A"
  ttl             = 60
  records         = [aws_eip.main.public_ip]
}
