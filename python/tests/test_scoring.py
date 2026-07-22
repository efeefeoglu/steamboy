import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from scoring import Scene,candidates,rank,remove_overlaps
class ScoringTests(unittest.TestCase):
 def setUp(self): self.scenes=[Scene(0,5,10,10,20),Scene(5,12,90,80,85),Scene(12,20,75,90,80),Scene(20,28,5,5,10,80)]
 def test_candidate_generation_combines_neighbors(self):
  found=candidates(self.scenes,8,20);self.assertTrue(any(x["start"]==5 and x["end"]==20 for x in found));self.assertTrue(all(8<=x["duration"]<=20 for x in found))
 def test_ranking_penalizes_black_static_content(self):
  best=rank(self.scenes,8,20,2)[0];self.assertEqual((best["start"],best["end"]),(5,20))
 def test_overlap_removal(self):
  items=[{"start":0,"end":10,"duration":10},{"start":1,"end":11,"duration":10},{"start":12,"end":22,"duration":10}]
  self.assertEqual(remove_overlaps(items,.55,5),[items[0],items[2]])
if __name__=="__main__": unittest.main()
