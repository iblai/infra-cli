# ibl.ai Infrastructure - AWS Single Server
# Provisions: VPC, EC2, ALB, S3, and optionally ACM/IAM certificates + Route53 DNS

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
  bucket_prefix   = var.bucket_suffix != "" ? "${var.project_name}-${var.environment}-${replace(var.base_domain, ".", "-")}-${var.bucket_suffix}" : "${var.project_name}-${var.environment}-${replace(var.base_domain, ".", "-")}"

  use_acm    = var.certificate_method == "acm"
  use_upload = var.certificate_method == "upload"
  use_https  = local.use_acm || local.use_upload

  # Certificate 1: API and core services
  certificate_domains_1 = [
    "api.${var.base_domain}",
    "apps.learn.${var.base_domain}",
    "asgi.data.${var.base_domain}",
    "base.manager.${var.base_domain}",
    "learn.${var.base_domain}",
    "llm.data.${var.base_domain}",
    "preview.learn.${var.base_domain}",
  ]

  # Certificate 2: Auth, monitoring, and SPA services
  certificate_domains_2 = [
    "studio.learn.${var.base_domain}",
    "os.${var.base_domain}",
    "meilisearch.learn.${var.base_domain}",
    "monitor.${var.base_domain}",
    "flowise.${var.base_domain}",
    "lms.${var.base_domain}",
    "platform.${var.base_domain}",
    "prometheus.${var.base_domain}",
  ]

  all_certificate_domains = concat(local.certificate_domains_1, local.certificate_domains_2)
}

# ---------------------------------------------------------------------------
# VPC & Networking
# ---------------------------------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "${local.resource_prefix}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${local.resource_prefix}-igw" }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${local.resource_prefix}-public-${count.index}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.resource_prefix}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ---------------------------------------------------------------------------
# Security Groups
# ---------------------------------------------------------------------------

resource "aws_security_group" "alb" {
  name        = "${local.resource_prefix}-alb-sg"
  description = "ALB - HTTP/HTTPS from anywhere"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.resource_prefix}-alb-sg" }
}

resource "aws_security_group" "ec2" {
  name        = "${local.resource_prefix}-ec2-sg"
  description = "EC2 - SSH from VPN IP, HTTP from ALB"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "SSH from VPN"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["${var.vpn_ip}/32"]
  }

  ingress {
    description     = "HTTP from ALB"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.resource_prefix}-ec2-sg" }
}

# ---------------------------------------------------------------------------
# SSH Key Pair
# ---------------------------------------------------------------------------

resource "aws_key_pair" "main" {
  count      = var.create_key_pair ? 1 : 0
  key_name   = "${local.resource_prefix}-key"
  public_key = var.ssh_public_key

  tags = { Name = "${local.resource_prefix}-key" }
}

# ---------------------------------------------------------------------------
# EC2 Instance
# ---------------------------------------------------------------------------

resource "aws_instance" "main" {
  ami                    = var.ami_id != "" ? var.ami_id : data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = var.create_key_pair ? aws_key_pair.main[0].key_name : var.existing_key_pair_name
  vpc_security_group_ids = [aws_security_group.ec2.id]
  subnet_id              = aws_subnet.public[0].id

  user_data = var.skip_user_data ? null : file("${path.module}/user_data.sh")

  root_block_device {
    volume_type = var.root_volume_type
    volume_size = var.root_volume_size
    encrypted   = true
  }

  tags = { Name = "${local.resource_prefix}-server" }
}

# ---------------------------------------------------------------------------
# S3 Buckets
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "backups" {
  bucket = "${local.bucket_prefix}-backups"
  tags   = { Name = "${local.bucket_prefix}-backups" }
}

resource "aws_s3_bucket" "dm_media" {
  bucket = "${local.bucket_prefix}-dm-media"
  tags   = { Name = "${local.bucket_prefix}-dm-media" }
}

resource "aws_s3_bucket" "dm_static" {
  bucket = "${local.bucket_prefix}-dm-static"
  tags   = { Name = "${local.bucket_prefix}-dm-static" }
}

resource "aws_s3_bucket_public_access_block" "dm_static" {
  bucket = aws_s3_bucket.dm_static.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "dm_static" {
  bucket = aws_s3_bucket.dm_static.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadGetObject"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.dm_static.arn}/*"
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.dm_static]
}

# ---------------------------------------------------------------------------
# Application Load Balancer
# ---------------------------------------------------------------------------

resource "aws_lb" "main" {
  name               = "${local.resource_prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  enable_deletion_protection = false
  enable_http2               = true

  tags = { Name = "${local.resource_prefix}-alb" }
}

resource "aws_lb_target_group" "main" {
  name     = "${local.resource_prefix}-tg"
  port     = 80
  protocol = "HTTP"
  vpc_id   = aws_vpc.main.id

  health_check {
    enabled             = true
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 5
    interval            = 30
    path                = "/"
    matcher             = "200"
  }

  tags = { Name = "${local.resource_prefix}-tg" }
}

resource "aws_lb_target_group_attachment" "main" {
  target_group_arn = aws_lb_target_group.main.arn
  target_id        = aws_instance.main.id
  port             = 80
}

# HTTP Listener - redirect to HTTPS when certs exist, otherwise forward
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  dynamic "default_action" {
    for_each = local.use_https ? [1] : []
    content {
      type = "redirect"
      redirect {
        port        = "443"
        protocol    = "HTTPS"
        status_code = "HTTP_301"
      }
    }
  }

  dynamic "default_action" {
    for_each = local.use_https ? [] : [1]
    content {
      type             = "forward"
      target_group_arn = aws_lb_target_group.main.arn
    }
  }
}

# ---------------------------------------------------------------------------
# ACM Certificates (when certificate_method = "acm")
# ---------------------------------------------------------------------------

resource "aws_acm_certificate" "main_1" {
  count = local.use_acm ? 1 : 0

  domain_name               = "api.data.${var.base_domain}"
  subject_alternative_names = local.certificate_domains_1
  validation_method         = "DNS"

  lifecycle { create_before_destroy = true }
  tags = { Name = "${local.resource_prefix}-cert-1" }
}

resource "aws_acm_certificate" "main_2" {
  count = local.use_acm ? 1 : 0

  domain_name               = "auth.${var.base_domain}"
  subject_alternative_names = local.certificate_domains_2
  validation_method         = "DNS"

  lifecycle { create_before_destroy = true }
  tags = { Name = "${local.resource_prefix}-cert-2" }
}

# Route53 zone data
data "aws_route53_zone" "main" {
  count   = local.use_acm ? 1 : 0
  zone_id = var.hosted_zone_id
}

# DNS validation records
resource "aws_route53_record" "cert_validation_1" {
  for_each = local.use_acm ? {
    for dvo in aws_acm_certificate.main_1[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  allow_overwrite = true
  zone_id         = data.aws_route53_zone.main[0].zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
}

resource "aws_route53_record" "cert_validation_2" {
  for_each = local.use_acm ? {
    for dvo in aws_acm_certificate.main_2[0].domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  } : {}

  allow_overwrite = true
  zone_id         = data.aws_route53_zone.main[0].zone_id
  name            = each.value.name
  type            = each.value.type
  records         = [each.value.record]
  ttl             = 60
}

resource "aws_acm_certificate_validation" "main_1" {
  count                   = local.use_acm ? 1 : 0
  certificate_arn         = aws_acm_certificate.main_1[0].arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation_1 : record.fqdn]

  depends_on = [aws_route53_record.cert_validation_1]
  timeouts { create = "10m" }
}

resource "aws_acm_certificate_validation" "main_2" {
  count                   = local.use_acm ? 1 : 0
  certificate_arn         = aws_acm_certificate.main_2[0].arn
  validation_record_fqdns = [for record in aws_route53_record.cert_validation_2 : record.fqdn]

  depends_on = [aws_route53_record.cert_validation_2]
  timeouts { create = "10m" }
}

# Route53 DNS A records (ALB aliases)
resource "aws_route53_record" "app" {
  for_each = local.use_acm ? toset(concat(
    ["api.data.${var.base_domain}", "auth.${var.base_domain}"],
    local.all_certificate_domains
  )) : toset([])

  allow_overwrite = true
  zone_id         = data.aws_route53_zone.main[0].zone_id
  name            = each.value
  type            = "A"

  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}

# ---------------------------------------------------------------------------
# Uploaded Certificate (when certificate_method = "upload")
# ---------------------------------------------------------------------------

resource "aws_iam_server_certificate" "uploaded" {
  count = local.use_upload ? 1 : 0

  name_prefix       = "${local.resource_prefix}-cert-"
  certificate_body  = file("${path.module}/${var.certificate_body_file}")
  private_key       = file("${path.module}/${var.certificate_key_file}")
  certificate_chain = var.certificate_chain_file != "" ? file("${path.module}/${var.certificate_chain_file}") : null

  lifecycle { create_before_destroy = true }
}

# ---------------------------------------------------------------------------
# HTTPS Listener (when any certificate method is used)
# ---------------------------------------------------------------------------

resource "aws_lb_listener" "https" {
  count = local.use_https ? 1 : 0

  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"

  certificate_arn = local.use_acm ? aws_acm_certificate_validation.main_1[0].certificate_arn : aws_iam_server_certificate.uploaded[0].arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.main.arn
  }
}

# Attach second ACM cert to HTTPS listener
resource "aws_lb_listener_certificate" "additional_cert" {
  count = local.use_acm ? 1 : 0

  listener_arn    = aws_lb_listener.https[0].arn
  certificate_arn = aws_acm_certificate_validation.main_2[0].certificate_arn
}
