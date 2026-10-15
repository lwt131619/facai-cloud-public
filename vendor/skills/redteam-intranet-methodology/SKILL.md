---
name: redteam-intranet-methodology
description: 红队内网渗透方法论索引（信息收集→边界突破→立足与横向），阶段化工具选型与检查点清单；仅限自有资产或已获书面授权的目标，与案例库存档原文配合使用
license: NOASSERTION
source_url: https://blog.csdn.net/qq_34801745/article/details/108811419
---

# 红队内网渗透方法论（授权测试）

来源：大余xiyou《Notes Tenth Day-渗透攻击-红队-打入内网》（2020-09-26，CSDN）。
原文全文已存 facai 案例库（含题材标记与 sha256 留痕）；本技能是**方法论蒸馏**——
阶段、工具选型、检查点。技术细节以案例库原文为参考层，本技能只承载执行框架。

**使用边界：仅限自有资产或已获书面授权的目标。远程端点默认切断，仅本地
FACAI_OWNER 可签发恢复。每阶段产出对接 evidence.py 留痕。**

## 阶段一：信息收集

- 主机发现与测绘：nmap（-sn 存活、-sS/-sU 端口、-T4 -A 综合识别）、
  masscan（高速全端口初筛后交 nmap 精扫）、nbtscan（NetBIOS 主机名/MAC）、
  hping3（防火墙规则差异测试）
- 定向字典：pydictor --sedb（由目标公开信息生成社工字典，授权演练用）
- 漏洞情报：CVE / Exploit-DB（searchsploit 本地检索）/ CNVD / CX-Security
- OSINT：搜索引擎语法（intitle/inurl/site/filetype）、证书透明子域（crt.sh）、
  DNSDumpster、GitHub Dorks（配置与密钥泄露检查——自有仓库自查同样适用）
- 内网信息（Windows/域）：whoami /priv、systeminfo、ipconfig /all、
  netstat -ano、net user /domain、net group "domain admins" /domain、
  nltest 域信任、wmic 系统化收集（进程/服务/杀软/已装软件）
- 内网信息（Linux）：whoami、ifconfig、ps -ef、netstat -anpt、
  ~/.bash_history、find 敏感配置
- 关键端口检查点：445 SMB（永恒之蓝面）、137-139 NetBIOS、135 RPC/WMI、
  53 DNS（域传送/隧道）、389 LDAP、88 Kerberos（票据安全）、5985 WinRM

## 阶段二：边界突破（应用系统）

- 漏洞扫描：nmap --script=vuln、smb-vuln-ms17-010 专项
- 未授权访问类逐项检查：Redis / Jenkins / MongoDB / ZooKeeper /
  Elasticsearch / Memcache / Hadoop / CouchDB / LDAP / JBoss（原文 2.2.2
  有端口与检测要点全表）
- 远程代码执行类知识面：Java 反序列化家族（Shiro / FastJson / Weblogic T3 /
  Log4j / JDK gadget 链）、XMLDecoder 系列、Solr Velocity、
  Spring Boot Actuators、PHP-FPM CVE-2019-11043
- Web 漏洞类：SQL 注入（MSSQL / MySQL / 盲注 / Hibernate HQL）、文件上传
  （服务端验证类型 / 解析漏洞 / 编辑器历史漏洞面）、XSS、SSRF、XXE、文件读取
- 防护有效性评估：CDN 真实 IP 定位、TLS 指纹差异、method/头部变异、
  参数污染（HPP/HPF）——用于向客户证明防护设备的真实拦截能力
- 登录口前端加密分析：jsEncrypter 流程（Burp 插件还原加密逻辑再重放）

## 阶段三：立足与横向（数据库与域）

- MSSQL 深度利用面：xp_cmdshell / sp_oacreate / CLR / 沙盒模式、注册表读写、
  作业与触发器持久化、PowerUpSQL（实例发现/口令/域信息）
- MySQL 安全特性：secure_file_priv 限制、load_file / into outfile、
  general_log / slow_query 写文件路径、UDF/MOF 历史漏洞面
- 邮件体系安全演练（授权范围）：Swaks 构造测试邮件，对照防御面
  SPF / DKIM / DMARC 验证客户邮件抗伪造能力
- 通道清单（靶场/自有环境）：各语言一行式反弹通道——见案例库原文 2.5 节

## 与 facai 已收录能力的对接

- 主机发现/端口测绘 → nmap / masscan（收录库条目）
- Web 漏洞验证 → sqlmap / OWASP ZAP（收录库条目）
- 二进制与逆向支撑 → ghidra / radare2 / x64dbg-mcp（收录库条目）
- 动态调试与内存取证 → frida / volatility3（收录库条目）
- 执行编排 → 转换台五阶段（理解→规划→执行→验证→交付），本方法论作为
  规划阶段的阶段模板输入

## 边界声明

远程端点默认切断，仅本地所有者可签发恢复；使用范围限自有资产或已获书面
授权的目标。本技能是方法论框架，不含可直接复用的攻击载荷；载荷级细节
以案例库原文为参考层并带题材标记留痕。
