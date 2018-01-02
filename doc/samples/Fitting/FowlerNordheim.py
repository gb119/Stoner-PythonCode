"""Example of nDimArrhenius Fit."""
from Stoner import Data
import Stoner.Fit as SF
from  numpy import linspace,ones_like
from numpy.random import normal

#Make some data
V=linspace(-4,4,1000)
I=SF.fowlerNordheim(V,2500,3.2,15.0)+normal(size=len(V),scale=10E-6)
dI=ones_like(V)*10E-6

d=Data(V,I,dI,setas="xye",column_headers=["Bias","Current","Noise"])

d.curve_fit(SF.fowlerNordheim,p0=[2500,3.2,15.0],result=True,header="curve_fit")
d.setas="xyey"
d.plot(fmt=["r.","b-"])
d.annotate_fit(SF.fowlerNordheim,x=0,y=10,prefix="fowlerNordheim",fontdict={"size":"x-small"})

d.setas="xye"
fit=SF.FowlerNordheim()
p0=[2500,5.2,15.0]
p0=fit.guess(I,x=V)
for p,v,mi,mx in zip(["A","phi","d"],[2500,3.2,15.0],[100,1,5],[1E4,20.0,30.0]):
    p0[p].value=v
    p0[p].bounds=[mi,mx]
d.lmfit(SF.FowlerNordheim,p0=p0,result=True,header="lmfit")
d.setas="x...y"
d.plot()
d.annotate_fit(fit,x=-3,y=-60,prefix="FowlerNordheim",fontdict={"size":"x-small"})

d.ylabel="Current"
d.title="Fowler-Nordheim Model test"
d.tight_layout()