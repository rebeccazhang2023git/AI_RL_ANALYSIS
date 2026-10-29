"""Flask web interface: one page + a small JSON API."""

from __future__ import annotations

import requests
from flask import Flask, Response, jsonify, render_template, request

from .. import config
from ..edgar import EdgarError
from ..service import AnalysisService


def create_app(service: AnalysisService | None = None) -> Flask:
    app = Flask(__name__)
    svc = service or AnalysisService()

    def company() -> str:
        return request.args.get("q") or (request.get_json(silent=True) or {}).get("q") \
            or config.DEFAULT_COMPANY

    def period() -> str | None:
        return request.args.get("period") or (request.get_json(silent=True) or {}).get("period")

    def inputs() -> dict:
        return (request.get_json(silent=True) or {}).get("inputs", {})

    @app.errorhandler(EdgarError)
    def edgar_error(e):
        return jsonify(error=str(e)), 400

    @app.errorhandler(KeyError)
    @app.errorhandler(ValueError)
    def bad_request(e):
        return jsonify(error=str(e).strip("'")), 400

    @app.errorhandler(requests.RequestException)
    def upstream_error(e):
        return jsonify(error=f"Could not reach SEC EDGAR: {e}"), 502

    @app.get("/")
    def index():
        return render_template("index.html", default_company=config.DEFAULT_COMPANY)

    @app.get("/api/overview")
    def overview():
        return jsonify(svc.overview(company()))

    @app.get("/api/snapshot")
    def snapshot():
        return jsonify(svc.snapshot(company(), period()))

    @app.post("/api/waterfall")
    def waterfall():
        return jsonify(svc.waterfall(company(), period(), inputs()))

    @app.post("/api/exchange")
    def exchange():
        return jsonify(svc.exchange(company(), period(), inputs()))

    @app.post("/api/runway")
    def runway():
        return jsonify(svc.runway(company(), period(), inputs()))

    @app.get("/api/export.csv")
    def export_csv():
        q = company()
        return Response(svc.csv(q), mimetype="text/csv", headers={
            "Content-Disposition": f"attachment; filename={q}_quarterly_panel.csv"})

    return app
