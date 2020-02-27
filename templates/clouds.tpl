clouds:
 default:
   region_name: {{ region }}
   identity_api_version: {{ api_version }}
   identity_interface: internal
   auth:
     username: {{ credentials_username }}
     password: {{ credentials_password }}
     project_name: {{ credentials_project }}
     project_domain_name: {{ credentials_project_domain_name }}
     user_domain_name: {{ credentials_user_domain_name }}
     auth_url: {{ credentials_protocol }}://{{ credentials_host }}:{{ credentials_port }}/v{{ api_version }}
     {% if ssl_ca -%}
     cacert: |
        {{ ssl_ca }}
     {% endif -%}
    verify: {% if ssl_ca %}true{% else %}false{% endif %}
