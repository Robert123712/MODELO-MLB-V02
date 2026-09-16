import unittest
from pathlib import Path
from unittest.mock import patch
import math
import numpy as np
import calibrar
import cierres
import precios
import modelo_diario as m
import validar
import valor


class RunLine(unittest.TestCase):
    """Cada equipo dando y recibiendo cada linea, no solo el par favorito."""

    def setUp(self):
        self.sim = m.simular_completo(4.2, 5.1)

    def test_every_line_for_both_teams(self):
        esperadas = {"-2.5", "-1.5", "+1.5", "+2.5"}
        self.assertEqual(set(self.sim["rl_casa"]), esperadas)
        self.assertEqual(set(self.sim["rl_visita"]), esperadas)

    def test_opposite_sides_are_complements(self):
        for linea in ("1.5", "2.5"):
            self.assertAlmostEqual(
                self.sim["rl_casa"][f"-{linea}"] + self.sim["rl_visita"][f"+{linea}"], 1)
            self.assertAlmostEqual(
                self.sim["rl_casa"][f"+{linea}"] + self.sim["rl_visita"][f"-{linea}"], 1)

    def test_giving_runs_is_never_easier_than_receiving_them(self):
        for equipo in ("rl_casa", "rl_visita"):
            mercado = self.sim[equipo]
            self.assertLessEqual(mercado["-2.5"], mercado["-1.5"])
            self.assertLessEqual(mercado["-1.5"], mercado["+1.5"])
            self.assertLessEqual(mercado["+1.5"], mercado["+2.5"])

    def test_classic_field_still_means_home_minus_1_5(self):
        self.assertEqual(self.sim["p_casa_rl"], self.sim["rl_casa"]["-1.5"])


def _evento(visita, casa, odds):
    return {"competitions": [{
        "competitors": [
            {"homeAway": "away", "team": {"displayName": visita}},
            {"homeAway": "home", "team": {"displayName": casa}},
        ],
        "odds": [odds] if odds else [],
    }]}


CIERRE_ESPN = {
    "provider": {"displayName": "ESPN BET"},
    "overUnder": 8.5,
    "spread": -1.5,
    "moneyline": {"home": {"close": {"odds": -145}}, "away": {"close": {"odds": 125}}},
    "pointSpread": {"home": {"close": {"line": -1.5}}},
}


class ClosingLine(unittest.TestCase):
    """El precio de cierre solo existe si se guarda: pasado el juego nadie lo republica."""

    def test_reads_the_closing_price_of_each_game(self):
        payload = {"events": [_evento("Miami Marlins", "Arizona Diamondbacks", CIERRE_ESPN)]}
        mercado = cierres.leer_marcador(payload)
        cierre = mercado[("Marlins", "Diamondbacks")]
        self.assertEqual(cierre["momio_casa"], -145)
        self.assertEqual(cierre["momio_visita"], 125)
        self.assertEqual(cierre["total"], 8.5)
        self.assertEqual(cierre["casa_de_apuestas"], "ESPN BET")

    def test_falls_back_to_the_flat_price_when_close_is_missing(self):
        odds = {"overUnder": 9.5, "homeTeamOdds": {"moneyLine": -110},
                "awayTeamOdds": {"moneyLine": -105}}
        cierre = cierres.leer_marcador({"events": [_evento("A Team", "B Team", odds)]})
        valores = next(iter(cierre.values()))
        self.assertEqual(valores["momio_casa"], -110)
        self.assertEqual(valores["total"], 9.5)

    def test_a_game_without_odds_is_skipped_not_invented(self):
        self.assertEqual(cierres.leer_marcador({"events": [_evento("A Team", "B Team", None)]}), {})
        vacio = {"provider": {"displayName": "x"}}
        self.assertEqual(cierres.leer_marcador({"events": [_evento("A", "B", vacio)]}), {})

    def test_counts_the_games_of_the_day_even_without_prices(self):
        """Un dia cubierto sin momios no se ve igual que un dia sin cobertura.

        Los dos terminan en cero cierres guardados, pero piden arreglos
        opuestos: si ESPN no cubre la fecha no hay nada que hacer aqui, y si la
        cubre sin precios significa que el cierre hay que pedirlo ANTES del
        juego, porque despues el proveedor ya lo quito.
        """
        sin_precios = {"events": [_evento("A Team", "B Team", None),
                                  _evento("C Team", "D Team", None)]}
        self.assertEqual(cierres.juegos_del_dia(sin_precios), 2)
        self.assertEqual(cierres.leer_marcador(sin_precios), {})
        self.assertEqual(cierres.juegos_del_dia({"events": []}), 0)

    def test_a_doubleheader_is_left_out(self):
        payload = {"events": [_evento("Miami Marlins", "Arizona Diamondbacks", CIERRE_ESPN),
                              _evento("Miami Marlins", "Arizona Diamondbacks", CIERRE_ESPN)]}
        self.assertEqual(cierres.leer_marcador(payload), {})

    def test_capture_matches_predictions_by_teams_and_date(self):
        payload = {"events": [_evento("Miami Marlins", "Arizona Diamondbacks", CIERRE_ESPN)]}
        predicciones = [
            {"fecha": "09/15/2026", "game_id": "1", "visita": "Miami Marlins",
             "casa": "Arizona Diamondbacks"},
            {"fecha": "09/14/2026", "game_id": "2", "visita": "Miami Marlins",
             "casa": "Arizona Diamondbacks"},
        ]
        filas = cierres.capturar("09/15/2026", predicciones, lambda url: payload)
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["game_id"], "1")
        self.assertEqual(filas[0]["momio_casa"], -145)

    def test_a_prediction_without_market_gets_no_price(self):
        payload = {"events": [_evento("Otro Team", "Mas Otro", CIERRE_ESPN)]}
        filas = cierres.capturar("09/15/2026", [
            {"fecha": "09/15/2026", "game_id": "1", "visita": "Miami Marlins",
             "casa": "Arizona Diamondbacks"}], lambda url: payload)
        self.assertEqual(filas, [])

    def test_pending_dates_exclude_today_and_what_is_already_saved(self):
        predicciones = [
            {"fecha": "09/12/2026", "game_id": "0"},
            {"fecha": "09/14/2026", "game_id": "1"},
            {"fecha": "09/15/2026", "game_id": "2"},
            {"fecha": "09/16/2026", "game_id": "3"},
        ]
        guardados = [{"fecha": "09/14/2026", "game_id": "1"}]
        # Hoy no, porque los juegos no han cerrado. La mas reciente primero:
        # los juegos de ayer valen mas que rellenar julio.
        self.assertEqual(
            cierres.fechas_pendientes(predicciones, guardados, "09/16/2026"),
            ["09/15/2026", "09/12/2026"])

    def test_a_network_failure_returns_nothing_instead_of_raising(self):
        def falla(url):
            raise OSError("sin red")
        self.assertEqual(cierres.capturar("09/15/2026", [], falla), [])


class GradedMarkets(unittest.TestCase):
    """Cada linea publicada se califica sola, con su muestra igual a los juegos."""

    def recolectar(self, predicciones, reales):
        import metricas_edgebook as me
        with patch.object(validar, "cargar_predicciones", return_value=predicciones), \
             patch.object(validar, "preparar_resultados", return_value=reales), \
             patch.object(validar, "_cargar_cache", return_value={}), \
             patch.object(validar, "_guardar_cache", lambda c: None):
            return me.recolectar()

    def datos(self):
        # Dos juegos: uno 7-2 (margen 5, total 9) y otro 1-3 (margen -2, total 4).
        prediccion = {
            "fecha": "09/16/2026", "visita": "A", "casa": "B", "model_version": "v1",
            "p_casa": "0.6", "p_casa_calibrada": "0.6", "total_esp": "8.5",
            "p_casa_f5": "0.55", "p_nrfi": "0.5",
            "rl_casa_m15": "0.4", "rl_casa_m25": "0.3",
            "rl_casa_p15": "0.7", "rl_casa_p25": "0.8",
            "tt_casa_45": "0.45", "tt_visita_45": "0.35",
        }
        predicciones = [
            {**prediccion, "game_id": "1"},
            {**prediccion, "game_id": "2"},
        ]
        for linea in ("55", "65", "75", "85", "95", "105", "115", "125"):
            for p in predicciones:
                p[f"p_over{linea}"] = "0.5"
        reales = {"09/16/2026": {
            ("1",): {"rv": 2, "rc": 7, "f5v": 1, "f5c": 3, "inn1": 0},
            ("2",): {"rv": 3, "rc": 1, "f5v": 2, "f5c": 0, "inn1": 2},
        }}
        return predicciones, reales

    def test_every_recorded_line_becomes_its_own_market(self):
        datos = self.recolectar(*self.datos())
        mercados = datos["versions"][0]["markets"]
        for esperado in ("moneyline", "total_over_55", "total_over_125",
                         "run_line_casa_m15", "run_line_casa_p25",
                         "team_total_casa_45", "team_total_visita_45",
                         "first_five", "nrfi"):
            self.assertIn(esperado, mercados, esperado)

    def test_sample_is_the_number_of_games_not_of_calls(self):
        datos = self.recolectar(*self.datos())
        version = datos["versions"][0]
        self.assertEqual(version["graded"], 2)
        for nombre, m in version["markets"].items():
            self.assertEqual(m["n"], 2, nombre)

    def test_run_line_is_graded_against_the_real_margin(self):
        datos = self.recolectar(*self.datos())
        mercados = datos["versions"][0]["markets"]
        # Margenes 5 y -2. El acierto compara el lado que el modelo favorecia
        # contra lo que paso, no si el evento ocurrio.
        # Dando 1.5 y 2.5 cubre solo el de margen 5, y el modelo se inclinaba a
        # que no cubriria: acierta uno de dos.
        self.assertEqual(mercados["run_line_casa_m15"]["hit_rate"], 0.5)
        self.assertEqual(mercados["run_line_casa_m25"]["hit_rate"], 0.5)
        # Recibiendo 1.5 cubre solo el de margen 5 y el modelo decia que si en
        # los dos: acierta uno. Recibiendo 2.5 cubre en ambos, incluido el que
        # perdio por exactamente 2: acierta los dos.
        self.assertEqual(mercados["run_line_casa_p15"]["hit_rate"], 0.5)
        self.assertEqual(mercados["run_line_casa_p25"]["hit_rate"], 1.0)

    def test_team_totals_use_each_team_runs(self):
        datos = self.recolectar(*self.datos())
        mercados = datos["versions"][0]["markets"]
        # Casa anoto 7 y 1: pasa de 4.5 una vez. Visita anoto 2 y 3: nunca.
        self.assertEqual(mercados["team_total_casa_45"]["hit_rate"], 0.5)
        self.assertEqual(mercados["team_total_visita_45"]["hit_rate"], 1.0)

    def test_publishes_how_often_the_event_happened(self):
        datos = self.recolectar(*self.datos())
        mercados = datos["versions"][0]["markets"]
        # La casa gano uno de dos juegos, y el modelo se inclinaba por ella.
        self.assertEqual(mercados["moneyline"]["base_rate"], 0.5)
        self.assertEqual(mercados["moneyline"]["hit_rate"], 0.5)
        # Casa recibiendo 2.5 cubrio en los dos: la regla fija de decir siempre
        # que si acierta el 100%, igual que el modelo. Sin este dato, su acierto
        # perfecto parece merito.
        self.assertEqual(mercados["run_line_casa_p25"]["base_rate"], 1.0)

    def test_bias_is_broken_down_by_month(self):
        predicciones, reales = self.datos()
        predicciones[1] = {**predicciones[1], "fecha": "08/01/2026"}
        reales["08/01/2026"] = {("2",): reales["09/16/2026"][("2",)]}
        del reales["09/16/2026"][("2",)]
        total = self.recolectar(predicciones, reales)["versions"][0]["projected_total"]
        meses = {m["month"]: m for m in total["by_month"]}
        self.assertEqual(sorted(meses), ["2026-08", "2026-09"])
        self.assertEqual(sum(m["n"] for m in meses.values()), total["n"])
        # 8.5 esperadas contra 4 reales en agosto: proyecto 4.5 de mas.
        self.assertEqual(meses["2026-08"]["bias"], 4.5)
        # Contra 9 reales en septiembre: proyecto 0.5 de menos.
        self.assertEqual(meses["2026-09"]["bias"], -0.5)

    def test_a_line_without_column_is_never_invented(self):
        predicciones, reales = self.datos()
        for p in predicciones:
            del p["rl_casa_m15"]
        mercados = self.recolectar(predicciones, reales)["versions"][0]["markets"]
        self.assertNotIn("run_line_casa_m15", mercados)
        self.assertIn("run_line_casa_m25", mercados)


class HistoricalRow(unittest.TestCase):
    """Lo que la pantalla muestra tiene que quedar registrado para poder calificarse."""

    def fila(self):
        sim = m.simular_completo(4.2, 5.1)
        f5_overs, p_casa5, p_visita5, p_empate5 = m.simular_f5(2.1, 2.4)
        r = {
            "visita": "Tigers", "casa": "Blue Jays",
            "abridor_v": "A", "abridor_c": "B",
            "lam_v": 4.2, "lam_c": 5.1, "total_esp": 9.3,
            "p_casa": 0.55, "p_casa_cruda": 0.54,
            "overs": sim["overs"], "tt_visita": sim["tt_visita"], "tt_casa": sim["tt_casa"],
            "rl_casa": sim["rl_casa"], "rl_visita": sim["rl_visita"],
            "p_casa_rl": sim["p_casa_rl"],
            "f5": {"total_esp": 4.5, "p_casa": p_casa5, "p_visita": p_visita5,
                   "p_empate": p_empate5, "overs": f5_overs,
                   "rl_casa": p_casa5 + p_empate5, "rl_visita": p_visita5 + p_empate5},
            "nrfi": {"nrfi": 0.52, "yrfi": 0.48},
            "generado_en": "2026-09-16T00:00:00Z",
        }
        juego = {"game_id": 777, "game_datetime": "2026-09-16T23:07:00Z"}
        return sim, dict(zip(m.COLUMNAS_HISTORICO, m.fila_historica("09/16/2026", juego, r)))

    def test_row_matches_the_header(self):
        sim, fila = self.fila()
        self.assertEqual(len(fila), len(m.COLUMNAS_HISTORICO))
        self.assertEqual(fila["game_id"], "777")

    def test_every_published_market_is_recorded(self):
        sim, fila = self.fila()
        for linea in m.LINEAS:
            self.assertEqual(fila[f"p_over{str(linea).replace('.', '')}"],
                             f"{sim['overs'][linea]:.3f}")
        for etiqueta, clave in (("m15", "-1.5"), ("m25", "-2.5"), ("p15", "+1.5"), ("p25", "+2.5")):
            self.assertEqual(fila[f"rl_casa_{etiqueta}"], f"{sim['rl_casa'][clave]:.3f}")
        for lado in ("visita", "casa"):
            for linea in m.LINEAS_TT:
                self.assertIn(f"tt_{lado}_{str(linea).replace('.', '')}", fila)

    def test_a_missing_market_is_not_written_silently(self):
        sim, _ = self.fila()
        r = {"visita": "A", "casa": "B"}
        with self.assertRaises((ValueError, KeyError)):
            m.fila_historica("09/16/2026", {"game_id": 1}, r)


class PublishedMarkets(unittest.TestCase):
    """Lo que el simulador calcula tiene que llegar al JSON que se publica."""

    def test_run_line_reaches_the_payload(self):
        import app
        sim = m.simular_completo(4.2, 5.1)
        publicado = app.mercados({**sim, "p_visita_rl": 1 - sim["p_casa_rl"]})
        for equipo in ("rl_casa", "rl_visita"):
            self.assertEqual(set(publicado[equipo]), {"-2.5", "-1.5", "+1.5", "+2.5"})
        self.assertEqual(publicado["p_casa_rl"], round(sim["rl_casa"]["-1.5"], 4))


class Markets(unittest.TestCase):
    def test_integer_push(self):
        p = valor.probabilidades_total({7.5: .6, 8.5: .4}, 8)
        np.testing.assert_allclose(p, [.4, .4, .2])
        self.assertAlmostEqual(valor.ev_por_unidad(p[0], 2, p[2]), 0)

    def test_no_extrapolation(self):
        self.assertIsNone(valor.probabilidades_total({7.5: .6, 8.5: .4}, 12))
        self.assertIsNone(valor.probabilidades_total({7.5: .6}, 8))
        self.assertIsNone(valor.probabilidades_total({}, 8.5))
        self.assertIsNone(valor.probabilidades_total({7.5: .4, 8.5: .6}, 8))

    def test_half_line_complements(self):
        self.assertEqual(valor.probabilidades_total({'8.5': .4}, 8.5), (.4, .6, 0))

    def test_paired_books(self):
        ev = {'bookmakers': [
            {'markets': [{'key': 'h2h', 'outcomes': [{'name': 'H', 'price': -200}, {'name': 'A', 'price': 160}]}]},
            {'markets': [{'key': 'h2h', 'outcomes': [{'name': 'H', 'price': -180}, {'name': 'A', 'price': 150}]}]}
        ]}
        expected = np.median([valor.devig_dos_vias(1/1.5, 1/2.6)[0], valor.devig_dos_vias(1/(1+100/180), 1/2.5)[0]])
        self.assertAlmostEqual(valor._consenso_pareado(ev, 'h2h', 'H', 'A')[0], expected)

    def test_total_ev_end_to_end(self):
        ev = {'bookmakers': [{'title': 'Book', 'markets': [{'key': 'totals', 'outcomes': [
            {'name': 'Over', 'price': 110, 'point': 8}, {'name': 'Under', 'price': -110, 'point': 8}]}]}]}
        picks = valor.analizar_juego(ev, 'A', 'H', .5, {7.5: .6, 8.5: .4})
        self.assertEqual(len(picks), 1)
        self.assertEqual(picks[0]['pick'], 'Over')
        self.assertAlmostEqual(picks[0]['p_push'], .2)
        self.assertAlmostEqual(picks[0]['ev'], .04)


class Temporal(unittest.TestCase):
    def test_no_future_or_same_day_in_train(self):
        fechas = [day for day in range(18) for _ in range(3)]
        seen = []
        for tr, te in calibrar.pliegues_temporales(fechas):
            self.assertLess(max(fechas[i] for i in tr), min(fechas[i] for i in te))
            seen.extend(te)
        self.assertEqual(len(seen), len(set(seen)))

    def test_identity_scores_same_holdout_samples(self):
        pairs = [(.2 if i % 2 else .8, i % 2) for i in range(60)]
        indices = [i for _, te in calibrar.pliegues_temporales(list(range(60))) for i in te]
        expected = sum((pairs[i][0] - pairs[i][1])**2 for i in indices)/len(indices)
        self.assertAlmostEqual(calibrar._cv_brier(pairs, lambda tr:(0,1)), expected)

    def test_unsorted_rejected(self):
        with self.assertRaises(ValueError):
            list(calibrar.pliegues_temporales([3,2,1,4,5,6]))


class Results(unittest.TestCase):
    @staticmethod
    def game(gid, final=True, innings=9):
        return {'gamePk':gid, 'status':{'detailedState':'Final' if final else 'Scheduled'},
                'teams':{'away':{'team':{'name':'A'},'score':1}, 'home':{'team':{'name':'H'},'score':2}},
                'linescore':{'innings':[{'away':{'runs':0},'home':{'runs':0}} for _ in range(innings)]}}

    def test_doubleheader_needs_id(self):
        with patch.object(validar.statsapi, 'get', return_value={'dates':[{'games':[self.game(1),self.game(2)]}]}):
            out=validar.resultados_de_fecha('09/01/2026', {})
        self.assertIsNone(validar.resultado_prediccion({'visita':'A','casa':'H'},out))
        self.assertEqual(validar.resultado_prediccion({'game_id':'2'},out)['game_id'],'2')

    def test_empty_cache_is_retried(self):
        cache={}
        with patch.object(validar.statsapi,'get',side_effect=[{'dates':[]},{'dates':[{'games':[self.game(1)]}]}]) as get:
            self.assertEqual(validar.resultados_de_fecha('09/01/2026',cache),{})
            self.assertTrue(validar.resultados_de_fecha('09/01/2026',cache))
            self.assertEqual(get.call_count,2)

    def test_post_start_predictions_excluded(self):
        import csv, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'pred.csv'
            fields=['fecha','visita','casa','game_id','generado_en','game_datetime']
            with path.open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
                for gid,created in [('1','2026-09-01T18:00:00Z'),('2','2026-09-01T20:00:00Z')]:
                    w.writerow(dict(zip(fields,['09/01/2026','A','H',gid,created,'2026-09-01T19:00:00Z'])))
            with patch.object(validar,'ARCHIVO_PRED',str(path)):
                self.assertEqual([p['game_id'] for p in validar.cargar_predicciones()],['1'])

    def test_short_game_has_no_f5(self):
        with patch.object(validar.statsapi,'get',return_value={'dates':[{'games':[self.game(1,innings=4)]}]}):
            out=validar.resultados_de_fecha('09/01/2026',{})
        self.assertIsNone(out[('1',)]['f5v'])


class Simulation(unittest.TestCase):
    def test_better_pitcher_reduces_hits(self):
        good=m.predecir_hits(.260,.200,1,1)
        bad=m.predecir_hits(.260,.300,1,1)
        self.assertLess(good['hits_esp'],bad['hits_esp'])
        for r in (good,bad):
            self.assertAlmostEqual(sum(r[k] for k in ('p_hit_0','p_hit_1','p_hit_2')),1,places=2)

    def test_probabilities(self):
        m._rng_local.gen=np.random.default_rng(123)
        full=m.simular_completo(4,5)
        self.assertAlmostEqual(sum(full['dist_total']),1)
        self.assertTrue(all(0<=p<=1 for p in full['overs'].values()))
        values=list(full['overs'].values())
        self.assertEqual(values,sorted(values,reverse=True))
        _,home,away,draw=m.simular_f5(2,3)
        self.assertAlmostEqual(home+away+draw,1)



class Pipeline(unittest.TestCase):
    def setUp(self):
        from contextlib import ExitStack
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        for name, result in {
            'datos_pitcher': {'fip':4.15,'ip_esp':5.5,'mano':'R','ip_temp':100},
            'carreras_por_juego':4.4,'factor_clima':1,'split_ofensivo':1,
            'alineacion_juego':None,'factor_lineup':1,
            'bullpen_stats':{'fip':4.15,'k9':8.8,'bb9':3.2},
            'factor_defensivo':1,'factor_fatiga_bullpen':1,
        }.items():
            self.stack.enter_context(patch.object(m,name,return_value=result))
        self.stack.enter_context(patch.object(m,'SEMILLA',123))
        self.game={'game_id':123,'game_datetime':'2026-09-09T23:00:00Z','status':'Scheduled',
                   'away_name':'A','home_name':'H','away_probable_pitcher':'P1','home_probable_pitcher':'P2'}

    def test_seed_independent_of_thread(self):
        from concurrent.futures import ThreadPoolExecutor
        direct=m.evaluar_juego(self.game,'09/09/2026',.56)
        with ThreadPoolExecutor(max_workers=2) as pool:
            other=pool.submit(m.evaluar_juego,self.game,'09/09/2026',.56).result()
        self.assertEqual(direct['p_casa'],other['p_casa'])
        np.testing.assert_array_equal(direct['dist_total'],other['dist_total'])
        self.assertEqual(direct['game_id'],123)

    def test_csv_migration_doubleheader_and_dedup(self):
        import csv, os, tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        import tracker
        with tempfile.TemporaryDirectory() as d:
            old=os.getcwd()
            try:
                os.chdir(d)
                with open('predicciones.csv','w') as f:
                    f.write('fecha,visita,casa,p_casa\n09/01/2026,A,H,0.55\n')
                games=[self.game,dict(self.game,game_id=124)]
                with patch.object(m.statsapi,'schedule',return_value=games), patch.object(m,'f5_frac_liga',return_value=.56), patch.object(valor,'obtener_odds',return_value={}), patch.object(tracker,'registrar'), redirect_stdout(StringIO()):
                    m.correr('09/09/2026')
                    m.correr('09/09/2026')
                with open('predicciones.csv') as f:
                    rows=list(csv.DictReader(f))
                self.assertEqual(len(rows),3)
                self.assertEqual(rows[0]['p_casa'],'0.55')
                self.assertEqual({r['game_id'] for r in rows[1:]},{'123','124'})
                self.assertTrue(rows[1]['generado_en'])
                self.assertTrue(rows[1]['p_casa_calibrada'])
            finally:
                os.chdir(old)

    def test_refresco_republica_sin_tocar_el_historico(self):
        # El historico admite una prediccion por juego y gana la primera. Si una
        # corrida de refresco escribiera, el dia quedaria registrado con la
        # prediccion menos informada —sin alineaciones, abridor estimado— y
        # validar.py calificaria esa en vez de la que la pagina muestra.
        import csv, os, tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        import tracker
        with tempfile.TemporaryDirectory() as d:
            old = os.getcwd()
            previo = os.environ.get("REGISTRAR_HISTORICO")
            try:
                os.chdir(d)
                games = [self.game]
                parches = lambda: (
                    patch.object(m.statsapi, 'schedule', return_value=games),
                    patch.object(m, 'f5_frac_liga', return_value=.56),
                    patch.object(valor, 'obtener_odds', return_value={}),
                    patch.object(tracker, 'registrar'),
                )
                os.environ["REGISTRAR_HISTORICO"] = "0"
                a, b, c, e = parches()
                with a, b, c, e, redirect_stdout(StringIO()):
                    m.correr('09/09/2026')
                self.assertFalse(os.path.exists('predicciones.csv'))

                # Y la corrida que si registra escribe con normalidad.
                os.environ["REGISTRAR_HISTORICO"] = "1"
                a, b, c, e = parches()
                with a, b, c, e, redirect_stdout(StringIO()):
                    m.correr('09/09/2026')
                with open('predicciones.csv') as f:
                    filas = list(csv.DictReader(f))
                self.assertEqual([r['game_id'] for r in filas], ['123'])
            finally:
                os.chdir(old)
                if previo is None:
                    os.environ.pop("REGISTRAR_HISTORICO", None)
                else:
                    os.environ["REGISTRAR_HISTORICO"] = previo


class Fits(unittest.TestCase):
    def test_degenerate_and_separated_data_are_finite(self):
        for pairs in [[(.5,1)]*100,[(.01,0),(.99,1)]*50,[(.99,0),(.01,1)]*50]:
            a,b=calibrar.ajustar_platt(pairs)
            self.assertTrue(math.isfinite(a) and math.isfinite(b))
            self.assertGreaterEqual(b,0)
            self.assertLessEqual(calibrar._logloss(calibrar._aplicar(pairs,a,b)),calibrar._logloss(pairs)+1e-9)


def _juego_espn(visita, casa, comienza, odds, identificador="401"):
    return {"id": identificador, "date": comienza,
            "competitions": [{"id": identificador, "date": comienza,
                              "competitors": [
                                  {"homeAway": "home", "team": {"displayName": casa}},
                                  {"homeAway": "away", "team": {"displayName": visita}}],
                              "odds": [odds] if odds else []}]}


PRECIO_VIVO = {"provider": {"displayName": "ESPN BET"}, "overUnder": 8.5, "spread": -1.5,
               "homeTeamOdds": {"moneyLine": -145}, "awayTeamOdds": {"moneyLine": 125}}
AHORA = "2026-09-16T18:00:00+00:00"


class PreciosObservados(unittest.TestCase):
    """El precio se anota mientras existe; despues del juego ya no hay que pedirlo."""

    def test_records_a_game_that_has_not_started(self):
        payload = {"events": [_juego_espn("Miami Marlins", "Arizona Diamondbacks",
                                          "2026-09-16T23:10Z", PRECIO_VIVO)]}
        fila = precios.observaciones(payload, AHORA)[0]
        self.assertEqual(fila["momio_casa"], -145)
        self.assertEqual(fila["momio_visita"], 125)
        self.assertEqual(fila["total"], 8.5)
        self.assertEqual(fila["run_line_casa"], -1.5)
        # 18:00 -> 23:10 son cinco horas y diez minutos. Ese hueco es lo que
        # despues permite decir si la observacion merece llamarse cierre.
        self.assertEqual(fila["minutos_antes"], 310)

    def test_a_game_already_started_is_not_a_forecast_anymore(self):
        payload = {"events": [_juego_espn("Miami Marlins", "Arizona Diamondbacks",
                                          "2026-09-16T17:00Z", PRECIO_VIVO)]}
        self.assertEqual(precios.observaciones(payload, AHORA), [])

    def test_a_game_without_price_is_skipped_not_blank(self):
        payload = {"events": [_juego_espn("A Team", "B Team", "2026-09-16T23:10Z", None)]}
        self.assertEqual(precios.observaciones(payload, AHORA), [])

    def test_a_doubleheader_keeps_both_games(self):
        """cierres.py tenia que tirarlas: cruzaba por nombres y el par apuntaba a dos
        juegos. Aqui cada juego trae su id y su hora, asi que se guardan los dos."""
        payload = {"events": [
            _juego_espn("Miami Marlins", "Arizona Diamondbacks", "2026-09-16T20:10Z",
                        PRECIO_VIVO, identificador="401a"),
            _juego_espn("Miami Marlins", "Arizona Diamondbacks", "2026-09-17T00:10Z",
                        PRECIO_VIVO, identificador="401b")]}
        filas = precios.observaciones(payload, AHORA)
        self.assertEqual(len(filas), 2)
        self.assertEqual({f["espn_id"] for f in filas}, {"401a", "401b"})
        self.assertNotEqual(filas[0]["minutos_antes"], filas[1]["minutos_antes"])

    def test_several_observations_of_one_game_are_all_kept(self):
        """La ultima antes del inicio es el cierre; las anteriores son el movimiento
        de linea, que el precio final ya no cuenta."""
        payload = lambda momio: {"events": [_juego_espn(
            "Miami Marlins", "Arizona Diamondbacks", "2026-09-16T23:10Z",
            {**PRECIO_VIVO, "homeTeamOdds": {"moneyLine": momio}})]}
        temprano = precios.observaciones(payload(-130), "2026-09-16T14:00:00+00:00")
        tarde = precios.observaciones(payload(-160), "2026-09-16T22:30:00+00:00")
        self.assertEqual(temprano[0]["momio_casa"], -130)
        self.assertEqual(tarde[0]["momio_casa"], -160)
        # Mismo juego, distinta hora: son dos hechos, no una correccion.
        self.assertEqual(temprano[0]["espn_id"], tarde[0]["espn_id"])
        self.assertLess(tarde[0]["minutos_antes"], temprano[0]["minutos_antes"])

    def test_saving_adds_without_ever_overwriting(self):
        """Una observacion es un hecho fechado. Si una corrida pisara a la
        anterior, el movimiento de linea se perderia y solo quedaria la ultima
        foto, que es justo lo que este archivo existe para evitar."""
        import tempfile
        fila = lambda hora, momio: {
            "fecha": "09/16/2026", "espn_id": "401", "comienza": "2026-09-16T23:10:00+00:00",
            "capturado_en": hora, "minutos_antes": 60, "visita": "Miami Marlins",
            "casa": "Arizona Diamondbacks", "momio_visita": 125, "momio_casa": momio,
            "total": 8.5, "run_line_casa": -1.5, "casa_de_apuestas": "ESPN BET"}
        with tempfile.TemporaryDirectory() as carpeta:
            original = precios.ARCHIVO
            precios.ARCHIVO = str(Path(carpeta) / "precios.csv")
            try:
                self.assertEqual(precios.guardar([fila("2026-09-16T14:00:00+00:00", -130)]), 1)
                self.assertEqual(precios.guardar([fila("2026-09-16T22:30:00+00:00", -160)]), 1)
                # La misma corrida repetida no duplica: mismo juego y misma hora.
                self.assertEqual(precios.guardar([fila("2026-09-16T22:30:00+00:00", -160)]), 0)
                guardadas = precios._leidas()
            finally:
                precios.ARCHIVO = original
        self.assertEqual([g["momio_casa"] for g in guardadas], ["-130", "-160"])

    def test_a_failed_request_writes_nothing(self):
        def falla(url):
            raise RuntimeError("sin red")
        self.assertEqual(precios.capturar("09/16/2026", falla), [])


if __name__ == "__main__":
    unittest.main()
