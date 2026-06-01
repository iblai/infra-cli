# ibl.ai Infrastructure — Optional AWS WAFv2 Web ACL on the ALB
#
# Gated by `var.enable_waf`. When disabled, none of these resources are
# created and the ALB serves traffic unfiltered.
#
# Rule layout (priorities run lowest-first; first terminal match wins):
#   8  allow-swagger          admin IPs may hit DM Swagger UI
#   9  restrict-swagger       block Swagger for everyone else
#   10 allow-studio           admin IPs may hit edX Studio (CMS)
#   12 restrict-studio        block Studio for everyone else
#   14 allow-admin            admin IPs may hit /admin/ (LMS/CMS/DM Django admin)
#   15 restrict-admin         block /admin/ for everyone else
#   20 allow-data             admin IPs may hit DM /data path
#   21 restrict-data          block /data path variants for everyone else
#   24 allow-edx              public access for learn.<base> + apps.learn.<base>
#                             (studio.learn.<base> is already blocked at 12)
#   40 block-dotfile-paths    block .git/.env/.htaccess/.svn/.hg/.ds_store
#   50-55 AWS managed rule groups (IpReputation, KnownBadInputs, Common,
#         SQLi, WordPress, PHP)
#
# Estimated WCU: ~1355 (under default 1500 limit). Default action: allow.

locals {
  use_waf = var.enable_waf
}

resource "aws_wafv2_ip_set" "admins" {
  count              = local.use_waf ? 1 : 0
  name               = "${local.resource_prefix}-admins"
  description        = "Admin IP whitelist for WAF allow rules"
  scope              = "REGIONAL"
  ip_address_version = "IPV4"
  addresses          = var.waf_allowed_ips

  tags = { Name = "${local.resource_prefix}-admins" }
}

resource "aws_wafv2_web_acl" "main" {
  count = local.use_waf ? 1 : 0
  name  = "${local.resource_prefix}-waf"
  scope = "REGIONAL"

  default_action {
    allow {}
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${local.resource_prefix}-waf"
    sampled_requests_enabled   = true
  }

  # -------------------------------------------------------------------------
  # 8: allow-swagger — admin IPs may reach DM Swagger UI
  # -------------------------------------------------------------------------
  rule {
    name     = "allow-swagger"
    priority = 8
    action {
      allow {}
    }
    statement {
      and_statement {
        statement {
          ip_set_reference_statement {
            arn = aws_wafv2_ip_set.admins[0].arn
          }
        }
        statement {
          byte_match_statement {
            search_string         = "/api/docs/schema/swagger-ui"
            positional_constraint = "STARTS_WITH"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "allow-swagger"
      sampled_requests_enabled   = true
    }
  }

  # -------------------------------------------------------------------------
  # 9: restrict-swagger — block Swagger UI for everyone else
  # -------------------------------------------------------------------------
  rule {
    name     = "restrict-swagger"
    priority = 9
    action {
      block {}
    }
    statement {
      byte_match_statement {
        search_string         = "/api/docs/schema/swagger-ui"
        positional_constraint = "STARTS_WITH"
        field_to_match {
          uri_path {}
        }
        text_transformation {
          priority = 0
          type     = "LOWERCASE"
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "restrict-swagger"
      sampled_requests_enabled   = true
    }
  }

  # -------------------------------------------------------------------------
  # 10: allow-studio — admin IPs may reach edX Studio (CMS)
  # -------------------------------------------------------------------------
  rule {
    name     = "allow-studio"
    priority = 10
    action {
      allow {}
    }
    statement {
      and_statement {
        statement {
          byte_match_statement {
            search_string         = "studio.learn.${var.base_domain}"
            positional_constraint = "EXACTLY"
            field_to_match {
              single_header {
                name = "host"
              }
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
        statement {
          ip_set_reference_statement {
            arn = aws_wafv2_ip_set.admins[0].arn
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "allow-studio"
      sampled_requests_enabled   = true
    }
  }

  # -------------------------------------------------------------------------
  # 12: restrict-studio — block Studio for everyone else
  # -------------------------------------------------------------------------
  rule {
    name     = "restrict-studio"
    priority = 12
    action {
      block {}
    }
    statement {
      byte_match_statement {
        search_string         = "studio.learn.${var.base_domain}"
        positional_constraint = "EXACTLY"
        field_to_match {
          single_header {
            name = "host"
          }
        }
        text_transformation {
          priority = 0
          type     = "LOWERCASE"
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "restrict-studio"
      sampled_requests_enabled   = true
    }
  }

  # -------------------------------------------------------------------------
  # 14: allow-admin — admin IPs may reach Django /admin/ on any host
  # -------------------------------------------------------------------------
  rule {
    name     = "allow-admin"
    priority = 14
    action {
      allow {}
    }
    statement {
      and_statement {
        statement {
          ip_set_reference_statement {
            arn = aws_wafv2_ip_set.admins[0].arn
          }
        }
        statement {
          byte_match_statement {
            search_string         = "/admin/"
            positional_constraint = "STARTS_WITH"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "allow-admin"
      sampled_requests_enabled   = true
    }
  }

  # -------------------------------------------------------------------------
  # 15: restrict-admin — block /admin/ for everyone else
  # -------------------------------------------------------------------------
  rule {
    name     = "restrict-admin"
    priority = 15
    action {
      block {}
    }
    statement {
      byte_match_statement {
        search_string         = "/admin/"
        positional_constraint = "STARTS_WITH"
        field_to_match {
          uri_path {}
        }
        text_transformation {
          priority = 0
          type     = "LOWERCASE"
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "restrict-admin"
      sampled_requests_enabled   = true
    }
  }

  # -------------------------------------------------------------------------
  # 20: allow-data — admin IPs may reach DM /data path
  # -------------------------------------------------------------------------
  rule {
    name     = "allow-data"
    priority = 20
    action {
      allow {}
    }
    statement {
      and_statement {
        statement {
          byte_match_statement {
            search_string         = "/data"
            positional_constraint = "STARTS_WITH"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
        statement {
          ip_set_reference_statement {
            arn = aws_wafv2_ip_set.admins[0].arn
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "allow-data"
      sampled_requests_enabled   = true
    }
  }

  # -------------------------------------------------------------------------
  # 21: restrict-data — block /data variants for everyone else
  # -------------------------------------------------------------------------
  rule {
    name     = "restrict-data"
    priority = 21
    action {
      block {}
    }
    statement {
      or_statement {
        statement {
          byte_match_statement {
            search_string         = "/data"
            positional_constraint = "STARTS_WITH"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
        statement {
          byte_match_statement {
            search_string         = "data/"
            positional_constraint = "STARTS_WITH"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
        statement {
          byte_match_statement {
            search_string         = "/data/"
            positional_constraint = "STARTS_WITH"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "restrict-data"
      sampled_requests_enabled   = true
    }
  }

  # -------------------------------------------------------------------------
  # 24: allow-edx — public access for LMS hosts
  # studio.learn.<base> is included here for parity with the source ruleset,
  # but is already blocked by `restrict-studio` at priority 12.
  # -------------------------------------------------------------------------
  rule {
    name     = "allow-edx"
    priority = 24
    action {
      allow {}
    }
    statement {
      or_statement {
        statement {
          byte_match_statement {
            search_string         = "studio.learn.${var.base_domain}"
            positional_constraint = "EXACTLY"
            field_to_match {
              single_header {
                name = "host"
              }
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
        statement {
          byte_match_statement {
            search_string         = "learn.${var.base_domain}"
            positional_constraint = "EXACTLY"
            field_to_match {
              single_header {
                name = "host"
              }
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
        statement {
          byte_match_statement {
            search_string         = "apps.learn.${var.base_domain}"
            positional_constraint = "EXACTLY"
            field_to_match {
              single_header {
                name = "host"
              }
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "allow-edx-studio"
      sampled_requests_enabled   = true
    }
  }

  # -------------------------------------------------------------------------
  # 40: block-dotfile-paths — defends against common discovery probes
  # (.git/HEAD, .env, .htaccess, .svn/entries, .hg/hgrc, .DS_Store)
  # -------------------------------------------------------------------------
  rule {
    name     = "block-dotfile-paths"
    priority = 40
    action {
      block {}
    }
    statement {
      or_statement {
        statement {
          byte_match_statement {
            search_string         = ".git"
            positional_constraint = "CONTAINS"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
            text_transformation {
              priority = 1
              type     = "COMPRESS_WHITE_SPACE"
            }
          }
        }
        statement {
          byte_match_statement {
            search_string         = ".env"
            positional_constraint = "CONTAINS"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
            text_transformation {
              priority = 1
              type     = "COMPRESS_WHITE_SPACE"
            }
          }
        }
        statement {
          byte_match_statement {
            search_string         = ".htaccess"
            positional_constraint = "CONTAINS"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
            text_transformation {
              priority = 1
              type     = "COMPRESS_WHITE_SPACE"
            }
          }
        }
        statement {
          byte_match_statement {
            search_string         = ".svn"
            positional_constraint = "CONTAINS"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
            text_transformation {
              priority = 1
              type     = "COMPRESS_WHITE_SPACE"
            }
          }
        }
        statement {
          byte_match_statement {
            search_string         = ".hg"
            positional_constraint = "CONTAINS"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
            text_transformation {
              priority = 1
              type     = "COMPRESS_WHITE_SPACE"
            }
          }
        }
        statement {
          byte_match_statement {
            search_string         = ".ds_store"
            positional_constraint = "CONTAINS"
            field_to_match {
              uri_path {}
            }
            text_transformation {
              priority = 0
              type     = "LOWERCASE"
            }
            text_transformation {
              priority = 1
              type     = "COMPRESS_WHITE_SPACE"
            }
          }
        }
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "block-dotfile-paths"
      sampled_requests_enabled   = true
    }
  }

  # -------------------------------------------------------------------------
  # 50-55: AWS Managed Rule Groups
  # -------------------------------------------------------------------------
  rule {
    name     = "AWS-AmazonIpReputationList"
    priority = 50
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesAmazonIpReputationList"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWS-AmazonIpReputationList"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWS-KnownBadInputsRuleSet"
    priority = 51
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWS-KnownBadInputsRuleSet"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWS-CommonRuleSet"
    priority = 52
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesCommonRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWS-CommonRuleSet"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWS-SQLiRuleSet"
    priority = 53
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesSQLiRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWS-SQLiRuleSet"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWS-WordPressRuleSet"
    priority = 54
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesWordPressRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWS-WordPressRuleSet"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWS-PHPRuleSet"
    priority = 55
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        vendor_name = "AWS"
        name        = "AWSManagedRulesPHPRuleSet"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "AWS-PHPRuleSet"
      sampled_requests_enabled   = true
    }
  }

  tags = { Name = "${local.resource_prefix}-waf" }
}

resource "aws_wafv2_web_acl_association" "main" {
  count        = local.use_waf ? 1 : 0
  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.main[0].arn
}
