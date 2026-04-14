"""
Flask dashboard for dmarcParser.
Serve with: dP serve [--port 5000] [--client NAME]
"""
import os
import sqlite3

from flask import Flask, render_template, request, abort, redirect

from .. import store, views


def _get_clients(index_db_path, filter_client=None):
    """Return list of client dicts from index.db, optionally filtered."""
    try:
        clients = store.list_all_clients_from_index(index_db_path)
    except Exception:
        clients = []
    if filter_client:
        clients = [c for c in clients if c["client_name"] == filter_client]
    return clients


def create_app(index_db_path=None, filter_client=None):
    if index_db_path is None:
        index_db_path = os.path.expanduser("~/.dmarcParser/index.db")

    app = Flask(__name__, template_folder="templates")
    app.config["INDEX_DB"] = index_db_path
    app.config["FILTER_CLIENT"] = filter_client

    # ------------------------------------------------------------------
    # Context processor: inject client list into every template
    # ------------------------------------------------------------------
    @app.context_processor
    def inject_clients():
        clients = _get_clients(app.config["INDEX_DB"], app.config["FILTER_CLIENT"])
        return {"nav_clients": clients, "filter_client": app.config["FILTER_CLIENT"]}

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        clients = _get_clients(app.config["INDEX_DB"], app.config["FILTER_CLIENT"])
        summaries = []
        for c in clients:
            db = c["db_path"]
            try:
                s = views.summary_data(db)
                s["client_name"] = c["client_name"]
                s["domain"] = c["domain"]
            except Exception:
                s = {"client_name": c["client_name"], "domain": c["domain"],
                     "reports": 0, "msgs": 0, "fails": 0, "fail_rate": 0,
                     "uniq_domains": 0, "uniq_ips": 0,
                     "date_start": None, "date_end": None}
            summaries.append(s)
        return render_template("index.html", summaries=summaries)

    @app.route("/client/<name>/summary")
    def client_summary(name):
        client = _resolve_client(name)
        days = request.args.get("days", type=int)
        data = views.summary_data(client["db_path"], days=days)
        return render_template("summary.html", client=client, data=data, days=days)

    @app.route("/client/<name>/ips")
    def client_ips(name):
        client = _resolve_client(name)
        days        = request.args.get("days",        type=int)
        limit       = request.args.get("limit",       type=int, default=50)
        sort        = request.args.get("sort",        default="fails")
        failed_only = request.args.get("failed_only") == "1"
        min_fails   = request.args.get("min_fails",   type=int, default=0)
        auth        = request.args.get("auth")        == "1"
        rows = views.ips_data(client["db_path"], limit=limit, days=days,
                              failed_only=failed_only, min_fails=min_fails,
                              sort=sort, auth_breakdown=auth)
        return render_template("ips.html", client=client, rows=rows,
                               days=days, limit=limit, sort=sort,
                               failed_only=failed_only, auth=auth)

    @app.route("/client/<name>/pct-timeline")
    def client_pct_timeline(name):
        client = _resolve_client(name)
        days = request.args.get("days", type=int, default=30)
        rows = views.pct_timeline_data(client["db_path"], days=days)
        return render_template("pct_timeline.html", client=client, rows=rows, days=days)

    @app.route("/client/<name>/senders")
    def client_senders(name):
        client = _resolve_client(name)
        days      = request.args.get("days",      type=int)
        limit     = request.args.get("limit",     type=int, default=50)
        sort      = request.args.get("sort",      default="fails")
        fail_only = request.args.get("fail_only") == "1"
        rows = views.senders_data(client["db_path"], limit=limit, sort=sort,
                                  days=days, fail_only=fail_only)
        return render_template("senders.html", client=client, rows=rows,
                               days=days, limit=limit, sort=sort, fail_only=fail_only)

    @app.route("/client/<name>/reporters")
    def client_reporters(name):
        client = _resolve_client(name)
        rows = views.reporters_data(client["db_path"])
        return render_template("reporters.html", client=client, rows=rows)

    @app.route("/client/<name>/reporter/<path:org_name>")
    def client_reporter_detail(name, org_name):
        client = _resolve_client(name)
        data = views.reporter_detail_data(client["db_path"], org_name)
        return render_template("reporter_detail.html", client=client, data=data)

    @app.route("/client/<name>/ip/<path:ip>")
    def client_ip_detail(name, ip):
        client = _resolve_client(name)
        data = views.ip_detail_data(client["db_path"], ip)
        return render_template("ip_detail.html", client=client, data=data)

    @app.route("/client/<name>/report/<fp_report>")
    def client_report_detail(name, fp_report):
        client = _resolve_client(name)
        data = views.report_detail_data(client["db_path"], fp_report)
        if not data:
            abort(404)
        return render_template("report_detail.html", client=client, data=data)

    @app.route("/client/<name>/sender/<path:domain>")
    def client_sender_detail(name, domain):
        client = _resolve_client(name)
        data = views.sender_detail_data(client["db_path"], domain)
        return render_template("sender_detail.html", client=client, data=data)

    @app.route("/client/<name>/delete", methods=["POST"])
    def client_delete(name):
        store.delete_client(app.config["INDEX_DB"], name)
        return redirect("/")

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _resolve_client(name):
        clients = _get_clients(app.config["INDEX_DB"], app.config["FILTER_CLIENT"])
        for c in clients:
            if c["client_name"] == name:
                return c
        abort(404)

    return app
