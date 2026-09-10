import unittest
from unittest.mock import patch
import math
import numpy as np
import calibrar
import modelo_diario as m
import validar
import valor


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


if __name__ == "__main__":
    unittest.main()
