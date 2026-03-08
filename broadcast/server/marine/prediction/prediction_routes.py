from __future__ import annotations

from quart import jsonify, request


def register_prediction_routes(app, predictor, cell_provider):
    @app.get('/marine/api/bait_forecast')
    async def bait_forecast():
        limit = int(request.args.get('limit', '2000'))
        cells = cell_provider(limit=min(2000, max(50, limit)))
        forecast = predictor.forecast_cells(cells)
        return jsonify({'cells': forecast['now'], 'horizons': forecast})
