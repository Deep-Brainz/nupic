# ----------------------------------------------------------------------
# Numenta Platform for Intelligent Computing (NuPIC)
# Copyright (C) 2014-2016, Numenta, Inc.  Unless you have an agreement
# with Numenta, Inc., for a separate license for this software code, the
# following terms and conditions apply:
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero Public License version 3 as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU Affero Public License for more details.
#
# You should have received a copy of the GNU Affero Public License
# along with this program.  If not, see http://www.gnu.org/licenses.
#
# http://numenta.org/licenses/
# ----------------------------------------------------------------------

"""
Temporal Memory implementation in Python.
"""

from collections import defaultdict
from operator import mul

from nupic.bindings.math import Random
from nupic.research.connections import Connections

from sys import maxint as MAX_INT
from bisect import bisect_left

EPSILON = 0.000001

def excitedColumnsGenerator(activeColumns,
                            activeSegments,
                            matchingSegments,
                            cellsPerColumn,
                            connections):
  """
  Generator used for iterating over the lists of active columns,
  active segments, and matching segments, each sorted by the column they
  correspond to.

  @param activeColumns    (list)   List of currently active columns
  @param activeSegments   (list)   List of segments active from lateral input
  @param matchingSegments (list)   List of segments matching from lateral input
  @param cellsPerColumn   (int)    Number of cells per column in the tm
  @param connections      (Object) Connections object of the tm

  @return (dict){
                  `column`               (int),
                  `isActiveColumn`       (bool),
                  `activeSegmentsBegin`  (int),
                  `activeSegmentsEnd`    (int),
                  `matchingSegmentsBegin`(int),
                  `matchingSegmentsEnd`  (int)
                }

  Notes:
    The ints returned (activeSegmentsBegin/End, matchingSegmentsBegin/End)
    represent bounds of indices into the lists activeSegments and
    matchingSegments. Together they form an interval of segments for the given
    column with the schema (note begin is closed and end is open): [begin, end).
  """
  activeColumnsProcessed = 0
  activeSegmentsProcessed = 0
  matchingSegmentsProcessed = 0

  activeColumnsNum    = len(activeColumns)
  activeSegmentsNum   = len(activeSegments)
  matchingSegmentsNum = len(matchingSegments)

  isActiveColumn = None
  while (activeColumnsProcessed < activeColumnsNum or
         activeSegmentsProcessed < activeSegmentsNum or
         matchingSegmentsProcessed < matchingSegmentsNum):

    currentColumn = MAX_INT
    if activeSegmentsProcessed < activeSegmentsNum:
      currentColumn = min(currentColumn,
                          connections.columnForSegment(
                            activeSegments[activeSegmentsProcessed],
                            cellsPerColumn))

    if matchingSegmentsProcessed < matchingSegmentsNum:
      currentColumn = min(currentColumn,
                          connections.columnForSegment(
                            matchingSegments[matchingSegmentsProcessed],
                            cellsPerColumn))

    if (activeColumnsProcessed < activeColumnsNum and
        activeColumns[activeColumnsProcessed] <= currentColumn):
      currentColumn = activeColumns[activeColumnsProcessed]
      isActiveColumn = True
      activeColumnsProcessed += 1
    else:
      isActiveColumn = False

    activeSegmentsBegin = activeSegmentsProcessed
    activeSegmentsEnd = activeSegmentsProcessed
    for i in xrange(activeSegmentsProcessed, activeSegmentsNum):
      if connections.columnForSegment(activeSegments[i],
                                      cellsPerColumn) == currentColumn:
        activeSegmentsProcessed += 1
        activeSegmentsEnd += 1
      else:
        break

    matchingSegmentsBegin = matchingSegmentsProcessed
    matchingSegmentsEnd = matchingSegmentsProcessed
    for i in xrange(matchingSegmentsProcessed, matchingSegmentsNum):
      if connections.columnForSegment(matchingSegments[i],
                                      cellsPerColumn) == currentColumn:
        matchingSegmentsProcessed += 1
        matchingSegmentsEnd += 1
      else:
        break

    yield {"column": currentColumn,
           "isActiveColumn": isActiveColumn,
           "activeSegmentsBegin": activeSegmentsBegin,
           "activeSegmentsEnd": activeSegmentsEnd,
           "matchingSegmentsBegin": matchingSegmentsBegin,
           "matchingSegmentsEnd": matchingSegmentsEnd
          }


class TemporalMemory(object):
  """
  Class implementing the Temporal Memory algorithm.
  """

  def __init__(self,
               columnDimensions=(2048,),
               cellsPerColumn=32,
               activationThreshold=13,
               initialPermanence=0.21,
               connectedPermanence=0.50,
               minThreshold=10,
               maxNewSynapseCount=20,
               permanenceIncrement=0.10,
               permanenceDecrement=0.10,
               predictedSegmentDecrement=0.0,
               maxSegmentsPerCell=255,
               maxSynapsesPerSegment=255,
               seed=42,
               **kwargs):
    """
    @param columnDimensions          (list)  Dimensions of the column space
    @param cellsPerColumn            (int)   Number of cells per column
    @param activationThreshold       (int)   If the number of active connected synapses on a segment is at least this threshold, the segment is said to be active.
    @param initialPermanence         (float) Initial permanence of a new synapse.
    @param connectedPermanence       (float) If the permanence value for a synapse is greater than this value, it is said to be connected.
    @param minThreshold              (int)   If the number of synapses active on a segment is at least this threshold, it is selected as the best matching cell in a bursting column.
    @param maxNewSynapseCount        (int)   The maximum number of synapses added to a segment during learning.
    @param permanenceIncrement       (float) Amount by which permanences of synapses are incremented during learning.
    @param permanenceDecrement       (float) Amount by which permanences of synapses are decremented during learning.
    @param predictedSegmentDecrement (float) Amount by which active permanences of synapses of previously predicted but inactive segments are decremented.
    @param seed                      (int)   Seed for the random number generator.

    Notes:

    predictedSegmentDecrement: A good value is just a bit larger than
    (the column-level sparsity * permanenceIncrement). So, if column-level
    sparsity is 2% and permanenceIncrement is 0.01, this parameter should be
    something like 4% * 0.01 = 0.0004).
    """
    # Error checking
    if not len(columnDimensions):
      raise ValueError("Number of column dimensions must be greater than 0")

    if cellsPerColumn <= 0:
      raise ValueError("Number of cells per column must be greater than 0")

    # TODO: Validate all parameters (and add validation tests)

    # Save member variables
    self.columnDimensions = columnDimensions
    self.cellsPerColumn = cellsPerColumn
    self.activationThreshold = activationThreshold
    self.initialPermanence = initialPermanence
    self.connectedPermanence = connectedPermanence
    self.minThreshold = minThreshold
    self.maxNewSynapseCount = maxNewSynapseCount
    self.permanenceIncrement = permanenceIncrement
    self.permanenceDecrement = permanenceDecrement
    self.predictedSegmentDecrement = predictedSegmentDecrement
    # Initialize member variables
    self.connections = Connections(self.numberOfCells(),
                                   maxSegmentsPerCell=maxSegmentsPerCell,
                                   maxSynapsesPerSegment=maxSynapsesPerSegment)
    self._random = Random(seed)

    self.activeCells = set()
    self.winnerCells = set()
    self.activeSegments = []
    self.matchingSegments = []

  # ==============================
  # Main functions
  # ==============================

  def compute(self, activeColumns, learn=True):
    """
    Feeds input record through TM, performing inference and learning.

    @param activeColumns (set)  Indices of active columns
    @param learn         (bool) Whether or not learning is enabled

    Updates member variables:
      - `activeCells`     (set)
      - `winnerCells`     (set)
      - `activeSegments`  (set)
      - `matchingSegments`(set)
    """
    prevActiveCells = sorted(self.activeCells)
    prevWinnerCells = sorted(self.winnerCells)

    activeColumns = sorted(activeColumns)

    self.activeCells = set()
    self.winnerCells = set()

    for excitedColumn in excitedColumnsGenerator(activeColumns,
                                                 self.activeSegments,
                                                 self.matchingSegments,
                                                 self.cellsPerColumn,
                                                 self.connections):
      if excitedColumn["isActiveColumn"]:
        if (excitedColumn["activeSegmentsBegin"] !=
            excitedColumn["activeSegmentsEnd"]):

          cellsToAdd = self.activatePredictedColumn(excitedColumn, learn,
                                                    prevActiveCells)
          self.activeCells.update(cellsToAdd)
          self.winnerCells.update(cellsToAdd)
        else:
          (cellsToAdd,
           winnerCell) = self.burstColumn(excitedColumn, learn,
                                          prevActiveCells, prevWinnerCells)
          self.activeCells.update(cellsToAdd)
          self.winnerCells.add(winnerCell)
      else:
        if learn:
          self.punishPredictedColumn(excitedColumn, prevActiveCells)

    (activeSegments,
     matchingSegments) = self.connections.computeActivity(self.activeCells,
                                                          self.connectedPermanence,
                                                          self.activationThreshold,
                                                          0.0,
                                                          self.minThreshold)

    self.activeSegments = activeSegments
    self.matchingSegments = matchingSegments

  def reset(self):
    """
    Indicates the start of a new sequence. Resets sequence state of the TM.
    """
    self.activeCells = set()
    self.activeSegments = set()
    self.winnerCells = set()


  def activatePredictedColumn(self, excitedColumn, learn, prevActiveCells):
    """
    Determines which cells in a predicted column should be added to
    winner cells list and calls adaptSegment on the segments that correctly
    predicted this column.

    @param excitedColumn   (dict)   Generated by excitedColumnsGenerator
    @param learn           (bool)   Determines if permanences are adjusted
    @param prevActiveCells (list)   Number of cells per column in the tm

    @return cellsToAdd (list) A list of predicted cells that will be added to
                              active cells and winner cells.
    """

    segIndex = excitedColumn["activeSegmentsBegin"]
    endIndex = excitedColumn["activeSegmentsEnd"]

    cellsToAdd = []
    newCell = True
    cell = None
    while segIndex < endIndex:
      active = self.activeSegments[segIndex]
      newCell = not cell == self.connections.cellForSegment(active)
      if newCell:
        cell = self.connections.cellForSegment(active)
        cellsToAdd.append(cell)
        newCell = False

      if learn:
        self.adaptSegment(prevActiveCells, self.permanenceIncrement,
                          self.permanenceDecrement, active)
      segIndex += 1

    return cellsToAdd


  def burstColumn(self, excitedColumn, learn,
                  prevActiveCells, prevWinnerCells):
    """
    @param excitedColumn   (dict)  Excited Column instance from excitedColumnsGenerator
    @param learn           (bool)  Whether or not learning is enabled
    @param prevActiveCells (list)  Active cells in `t-1`
    @param prevWinnerCells (list)  Winner cells in `t-1`

    @return (tuple) Contains:
                      `cells`         (set),
                      `bestCell`      (set),
    """
    cells = self.cellsForColumn(excitedColumn["column"])

    if excitedColumn["matchingSegmentsBegin"] != excitedColumn["matchingSegmentsEnd"]:
      (bestSegment, overlap) = self.bestMatchingSegment(excitedColumn, prevActiveCells)
      bestCell = self.connections.cellForSegment(bestSegment)
      if learn:
        self.adaptSegment(prevActiveCells, self.permanenceIncrement,
                          self.permanenceDecrement, bestSegment)

        nGrowDesired = self.maxNewSynapseCount - overlap

        if nGrowDesired > 0:
          self.growSynapses(nGrowDesired, prevWinnerCells, bestSegment)
    else:
      bestCell = self.leastUsedCell(cells)
      if learn:
        nGrowExact = min(self.maxNewSynapseCount, len(prevWinnerCells))
        if nGrowExact > 0:
          bestSegment = self.connections.createSegment(bestCell)
          self.growSynapses(nGrowExact, prevWinnerCells, bestSegment)



    return cells, bestCell

  def punishPredictedColumn(self, excitedColumn, prevActiveCells):
    """
    Punishes the Segments that incorrectly predicted a column to be active.

    @param excitedColumn   (dict) Excited Column instance from excitedColumnsGenerator
    @param prevActiveCells (list) Active cells in `t-1`

    """
    if self.predictedSegmentDecrement > 0.0:
      for matchingIndex in xrange(excitedColumn["matchingSegmentsBegin"],
                                  excitedColumn["matchingSegmentsEnd"]):
        self.adaptSegment(prevActiveCells, -self.predictedSegmentDecrement,
                          0.0, self.matchingSegments[matchingIndex])

  # ==============================
  # Helper functions
  # ==============================

  def bestMatchingSegment(self, excitedColumn, prevActiveCells):
    """
    Gets the segment on a cell with the largest number of activate synapses.
    Returns an int representing the segment and the number of synapses
    corresponding to it.

    @param excitedColumn   (dict) Excited Column instance from excitedColumnsGenerator
    @param prevActiveCells (set)  Active cells in `t-1`

    @return (tuple) Contains:
                      `bestSegment`                 (int),
                      `bestNumActiveSynapses`       (int)
    """
    maxSynapses = 0
    bestSegment = None
    bestNumActiveSynapses = None

    for i in xrange(excitedColumn["matchingSegmentsBegin"],
                    excitedColumn["matchingSegmentsEnd"]):
      numActiveSynapses = 0

      for syn in self.connections.synapsesForSegment(self.matchingSegments[i]):
        synapseData = self.connections.dataForSynapse(syn)
        if self.binSearch(prevActiveCells, synapseData.presynapticCell) != -1:
          numActiveSynapses += 1

      if numActiveSynapses >= maxSynapses:
        maxSynapses = numActiveSynapses
        bestSegment = self.matchingSegments[i]
        bestNumActiveSynapses = numActiveSynapses

    return bestSegment, bestNumActiveSynapses


  def leastUsedCell(self, cells):
    """
    Gets the cell with the smallest number of segments.
    Break ties randomly.

    @param cells       (set)         Indices of cells

    @return (int) Cell index
    """
    leastUsedCells = set()
    minNumSegments = float("inf")

    for cell in cells:
      numSegments = len(self.connections.segmentsForCell(cell))

      if numSegments < minNumSegments:
        minNumSegments = numSegments
        leastUsedCells = set()

      if numSegments == minNumSegments:
        leastUsedCells.add(cell)

    i = self._random.getUInt32(len(leastUsedCells))
    return sorted(leastUsedCells)[i]


  def growSynapses(self, nDesiredNewSynapes, prevWinnerCells, segment):
    """
    @params nDesiredNewSynapes (int)  Desired number of synapses to grow
    @params prevWinnerCells    (list) Winner cells in `t-1`
    @param  segment            (int)  Segment to grow synapses on.

    Notes: The process of swapping the last value into the index in the array
    that was most recently changed is to ensure the same results that we get
    in the c++ implentation using iter_swap with vectors.
    """
    candidates = set(prevWinnerCells)

    for synapse in self.connections.synapsesForSegment(segment):
      presynapticCell = self.connections.dataForSynapse(synapse).presynapticCell

      if presynapticCell in candidates:
        candidates.remove(presynapticCell)

    candidates = sorted(candidates)
    candidatesLength = len(candidates)
    nActual = min(nDesiredNewSynapes, candidatesLength)

    for _ in range(nActual):
      rand = self._random.getUInt32(candidatesLength)
      self.connections.createSynapse(segment, candidates[rand],
                                     self.initialPermanence)
      candidates[rand] = candidates[candidatesLength - 1]
      candidatesLength -= 1


  def adaptSegment(self, prevActiveCells, permanenceIncrement,
                   permanenceDecrement, segment):
    """
    Updates synapses on segment.
    Strengthens active synapses; weakens inactive synapses.

    @param prevActiveCells      (list)   Active cells in `t-1`
    @param permanenceIncrement  (float)  Amount to increment active synapses
    @param permanenceDecrement  (float)  Amount to decrement inactive synapses
    @param segment              (int)    Segment to adapt
    """
    # Need to copy synapses for segment set below because it will be modified
    # during iteration by `destroySynapse`
    for synapse in set(self.connections.synapsesForSegment(segment)):
      synapseData = self.connections.dataForSynapse(synapse)
      permanence = synapseData.permanence

      if self.binSearch(prevActiveCells, synapseData.presynapticCell) != -1:
        permanence += permanenceIncrement
      else:
        permanence -= permanenceDecrement

      # Keep permanence within min/max bounds
      permanence = max(0.0, min(1.0, permanence))

      if permanence < EPSILON:
        self.connections.destroySynapse(synapse)
      else:
        self.connections.updateSynapsePermanence(synapse, permanence)

    # awaiting change to connections.py to facilitate deleting segments
    # and synapses like the c++ implementation.
    # if (len(self.connections.synapsesForSegment(segment)) == 0):
    #   self.connections.destroySegment(segment)

  @staticmethod
  def binSearch(arr, val):
    i = bisect_left(arr, val)
    if i != len(arr) and arr[i] == val:
      return i
    return -1

  def columnForCell(self, cell):
    """
    Returns the index of the column that a cell belongs to.

    @param cell (int) Cell index

    @return (int) Column index
    """
    self._validateCell(cell)

    return int(cell / self.cellsPerColumn)


  def cellsForColumn(self, column):
    """
    Returns the indices of cells that belong to a column.

    @param column (int) Column index

    @return (set) Cell indices
    """
    self._validateColumn(column)

    start = self.cellsPerColumn * self.getCellIndex(column)
    end = start + self.cellsPerColumn
    return set(xrange(start, end))


  def numberOfColumns(self):
    """
    Returns the number of columns in this layer.

    @return (int) Number of columns
    """
    return reduce(mul, self.columnDimensions, 1)


  def numberOfCells(self):
    """
    Returns the number of cells in this layer.

    @return (int) Number of cells
    """
    return self.numberOfColumns() * self.cellsPerColumn


  def getActiveCells(self):
    """
    Returns the indices of the active cells.

    @return (list) Indices of active cells.
    """
    return self.getCellIndices(self.activeCells)


  def getPredictiveCells(self):
    """
    Returns the indices of the predictive cells.

    @return (list) Indices of predictive cells.
    """
    predictiveCells = set()
    for activeSegment in self.activeSegments:
      cell = self.connections.cellForSegment(activeSegment)
      if not cell in predictiveCells:
        predictiveCells.add(cell)

    return sorted(predictiveCells)


  def getWinnerCells(self):
    """
    Returns the indices of the winner cells.

    @return (list) Indices of winner cells.
    """
    return self.getCellIndices(self.winnerCells)


  def getCellsPerColumn(self):
    """
    Returns the number of cells per column.

    @return (int) The number of cells per column.
    """
    return self.cellsPerColumn


  def mapCellsToColumns(self, cells):
    """
    Maps cells to the columns they belong to

    @param cells (set) Cells

    @return (dict) Mapping from columns to their cells in `cells`
    """
    cellsForColumns = defaultdict(set)

    for cell in cells:
      column = self.columnForCell(cell)
      cellsForColumns[column].add(cell)

    return cellsForColumns


  def write(self, proto):
    """
    Writes serialized data to proto object

    @param proto (DynamicStructBuilder) Proto object
    """
    proto.columnDimensions = self.columnDimensions
    proto.cellsPerColumn = self.cellsPerColumn
    proto.activationThreshold = self.activationThreshold
    proto.initialPermanence = self.initialPermanence
    proto.connectedPermanence = self.connectedPermanence
    proto.minThreshold = self.minThreshold
    proto.maxNewSynapseCount = self.maxNewSynapseCount
    proto.permanenceIncrement = self.permanenceIncrement
    proto.permanenceDecrement = self.permanenceDecrement
    proto.predictedSegmentDecrement = self.predictedSegmentDecrement

    self.connections.write(proto.connections)
    self._random.write(proto.random)

    proto.activeCells = list(self.activeCells)
    proto.activeSegments = list(self.activeSegments)
    proto.winnerCells = list(self.winnerCells)
    proto.matchingSegments = list(self.matchingSegments)


  @classmethod
  def read(cls, proto):
    """
    Reads deserialized data from proto object

    @param proto (DynamicStructBuilder) Proto object

    @return (TemporalMemory) TemporalMemory instance
    """
    tm = object.__new__(cls)

    tm.columnDimensions = list(proto.columnDimensions)
    tm.cellsPerColumn = int(proto.cellsPerColumn)
    tm.activationThreshold = int(proto.activationThreshold)
    tm.initialPermanence = proto.initialPermanence
    tm.connectedPermanence = proto.connectedPermanence
    tm.minThreshold = int(proto.minThreshold)
    tm.maxNewSynapseCount = int(proto.maxNewSynapseCount)
    tm.permanenceIncrement = proto.permanenceIncrement
    tm.permanenceDecrement = proto.permanenceDecrement
    tm.predictedSegmentDecrement = proto.predictedSegmentDecrement

    tm.connections = Connections.read(proto.connections)
    tm._random = Random()
    tm._random.read(proto.random)

    tm.activeCells = set([int(x) for x in proto.activeCells])
    tm.activeSegments = [int(x) for x in proto.activeSegments]
    tm.winnerCells = set([int(x) for x in proto.winnerCells])
    tm.matchingSegments = [int(x) for x in proto.matchingSegments]

    return tm


  def __eq__(self, other):
    """
    Equality operator for TemporalMemory instances.
    Checks if two instances are functionally identical
    (might have different internal state).

    @param other (TemporalMemory) TemporalMemory instance to compare to
    """
    if self.columnDimensions != other.columnDimensions: return False
    if self.cellsPerColumn != other.cellsPerColumn: return False
    if self.activationThreshold != other.activationThreshold: return False
    if abs(self.initialPermanence - other.initialPermanence) > EPSILON:
      return False
    if abs(self.connectedPermanence - other.connectedPermanence) > EPSILON:
      return False
    if self.minThreshold != other.minThreshold: return False
    if self.maxNewSynapseCount != other.maxNewSynapseCount: return False
    if abs(self.permanenceIncrement - other.permanenceIncrement) > EPSILON:
      return False
    if abs(self.permanenceDecrement - other.permanenceDecrement) > EPSILON:
      return False
    if abs(self.predictedSegmentDecrement - other.predictedSegmentDecrement) > EPSILON:
      return False

    if self.connections != other.connections: return False
    if self.activeCells != other.activeCells: return False
    if self.winnerCells != other.winnerCells: return False
    if self.matchingSegments != other.matchingSegments: return False
    if self.activeSegments != other.activeSegments: return False


    return True


  def __ne__(self, other):
    """
    Non-equality operator for TemporalMemory instances.
    Checks if two instances are not functionally identical
    (might have different internal state).

    @param other (TemporalMemory) TemporalMemory instance to compare to
    """
    return not self.__eq__(other)


  def _validateColumn(self, column):
    """
    Raises an error if column index is invalid.

    @param column (int) Column index
    """
    if column >= self.numberOfColumns() or column < 0:
      raise IndexError("Invalid column")


  def _validateCell(self, cell):
    """
    Raises an error if cell index is invalid.

    @param cell (int) Cell index
    """
    if cell >= self.numberOfCells() or cell < 0:
      raise IndexError("Invalid cell")


  @classmethod
  def getCellIndices(cls, cells):
    return [cls.getCellIndex(c) for c in cells]


  @staticmethod
  def getCellIndex(cell):
    return cell
