# Master mock

## Heartbeat protocol

The patched dedicated server uses plain HTTP and calls:

```text
GET /handlerservers.ashx?type=ping&keys=&port=<server port>
GET /handlerservers.ashx?type=confirmping&port=<server port>&rand=<challenge>
```

After `confirmping` succeeds, the source IP and requested game port are kept in
memory for `--ttl` seconds. `type=list` returns those live entries together with
the optional static entries in `servers.txt`.

```text
GET /handlerservers.ashx?type=list
```

`type=remove` removes an entry immediately. If a server stops without sending
`remove`, the TTL removes it automatically.

## Run

The production domain can accept plain HTTP on TCP port 80:

```powershell
python .\master_server.py --listen 0.0.0.0 --port 80 --ttl 600
```

The dedicated server now supports a custom port in `-master`. For local testing,
run the mock on the same port:

```powershell
python .\master_server.py --listen 127.0.0.1 --port 18080 --ttl 600
mb_warband_dedicated.exe -r server_config.txt -m Native -master 127.0.0.1:18080
```

`servers.txt` is optional. Put one static `IP:port` entry per line; blank lines
and lines beginning with `#` are ignored. Heartbeat entries are dynamic and are
not written back to this file.